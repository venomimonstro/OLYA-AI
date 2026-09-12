from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillingCheckout, PaymentProviderAttempt, User
from app.services.auth import get_current_user
from app.services.billing import (
    BillingConflictError,
    BillingNotFoundError,
    BillingValidationError,
    apply_payment_record,
    create_checkout,
)
from app.services.commerce import ingest_payment
from app.services.payment_providers import (
    YOOMONEY_CONFIRM_URL,
    PaymentProviderUnavailable,
    PaymentProviderValidationError,
    create_yookassa_payment,
    get_or_create_attempt,
    provider_readiness,
    require_provider,
    validate_yookassa_against_checkout,
    verify_yookassa_payment,
    verify_yoomoney_notification,
    yoomoney_form,
)

router = APIRouter(prefix="/v1/commerce", tags=["payment-providers"])
MAX_WEBHOOK_BODY = 64 * 1024


class ProviderCheckoutCreate(BaseModel):
    plan: str = Field(min_length=1, max_length=24)
    provider: str = Field(min_length=1, max_length=24)
    idempotency_key: str = Field(min_length=8, max_length=80)


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PaymentProviderUnavailable):
        return HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "10"})
    if isinstance(exc, (PaymentProviderValidationError, BillingValidationError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, BillingNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _attempt_dict(row: PaymentProviderAttempt, checkout: BillingCheckout) -> dict:
    return {
        "attempt_id": row.id,
        "checkout_id": checkout.id,
        "plan": checkout.plan,
        "amount_minor": int(checkout.amount_minor),
        "currency": checkout.currency,
        "checkout_status": checkout.status,
        "provider": row.provider,
        "provider_payment_id": row.provider_payment_id,
        "provider_status": row.status,
        "confirmation_url": row.confirmation_url or None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _bounded_content_length(request: Request) -> None:
    raw = request.headers.get("content-length", "").strip()
    if raw:
        try:
            size = int(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if size < 0 or size > MAX_WEBHOOK_BODY:
            raise HTTPException(status_code=413, detail="Payment webhook body is too large")


@router.get("/billing/providers")
def billing_providers(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    _ = user
    return provider_readiness(db, request.app.state.settings)


@router.post("/billing/provider-checkout", status_code=status.HTTP_201_CREATED)
async def provider_checkout(
    payload: ProviderCheckoutCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        provider, _ = require_provider(db, request.app.state.settings, payload.provider)
        checkout = create_checkout(
            db,
            user,
            request.app.state.settings,
            plan=payload.plan,
            idempotency_key=payload.idempotency_key,
            direct_provider_ready=True,
        )
        if checkout.status != "pending":
            raise BillingConflictError(f"Checkout is already {checkout.status}")
        attempt = get_or_create_attempt(db, checkout, provider)
        if provider == "yookassa":
            result = await create_yookassa_payment(db, request.app.state.settings, checkout, attempt)
            attempt.confirmation_url = str(result.get("confirmation_url") or "")
            attempt.status = str(result.get("status") or "pending")
            redirect_url = attempt.confirmation_url
        else:
            yoomoney_form(db, request.app.state.settings, checkout, attempt)
            redirect_url = f"/v1/commerce/billing/yoomoney/{attempt.id}/pay"
        db.commit()
        db.refresh(attempt)
        return {**_attempt_dict(attempt, checkout), "redirect_url": redirect_url}
    except (PaymentProviderUnavailable, PaymentProviderValidationError, BillingValidationError, BillingConflictError, BillingNotFoundError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc


@router.get("/billing/provider-attempts")
def provider_attempts(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list(
        db.execute(
            select(PaymentProviderAttempt, BillingCheckout)
            .join(BillingCheckout, BillingCheckout.id == PaymentProviderAttempt.checkout_id)
            .where(BillingCheckout.user_id == user.id)
            .order_by(PaymentProviderAttempt.created_at.desc())
            .limit(limit)
        ).all()
    )
    return [_attempt_dict(attempt, checkout) for attempt, checkout in rows]


@router.get("/billing/yoomoney/{attempt_id}/pay", response_class=HTMLResponse, include_in_schema=False)
def yoomoney_pay(
    attempt_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    attempt = db.get(PaymentProviderAttempt, attempt_id)
    if attempt is None or attempt.provider != "yoomoney":
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    checkout = db.get(BillingCheckout, attempt.checkout_id)
    if checkout is None or checkout.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    if checkout.status != "pending":
        raise HTTPException(status_code=409, detail=f"Checkout is already {checkout.status}")
    try:
        fields = yoomoney_form(db, request.app.state.settings, checkout, attempt)
    except (PaymentProviderUnavailable, PaymentProviderValidationError) as exc:
        raise _provider_error(exc) from exc
    db.commit()
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(key, quote=True)}" value="{html.escape(value, quote=True)}">'
        for key, value in fields.items()
    )
    nonce = __import__("secrets").token_urlsafe(18)
    document = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Оплата X1 через ЮMoney</title><style nonce="{nonce}">body{{margin:0;background:#090b10;color:#f4f6fa;font:16px/1.5 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{width:min(440px,calc(100% - 28px));padding:26px;background:#111620;border:1px solid #29313d;border-radius:16px}}button{{width:100%;padding:12px;border:0;border-radius:10px;background:#d34747;color:white;font:700 16px system-ui;cursor:pointer}}p{{color:#a4adba}}</style></head><body><main><h1>Оплата тарифа {html.escape(checkout.plan.upper())}</h1><p>Сумма к списанию: {html.escape(fields['sum'])} ₽. Оплата откроется на защищённой странице ЮMoney.</p><form method="post" action="{YOOMONEY_CONFIRM_URL}">{hidden}<button type="submit">Перейти к оплате</button></form></main></body></html>'''
    response = HTMLResponse(document)
    response.headers.update({
        "Content-Security-Policy": f"default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action {YOOMONEY_CONFIRM_URL}; style-src 'nonce-{nonce}'",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
    return response


@router.post("/payments/yoomoney/notify", response_class=PlainTextResponse, include_in_schema=False)
async def yoomoney_notify(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    _bounded_content_length(request)
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payment webhook body is too large")
    try:
        text = body.decode("utf-8", errors="strict")
        pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict", max_num_fields=64)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate field")
        params = dict(pairs)
        checkout = verify_yoomoney_notification(db, request.app.state.settings, params)
        operation_id = str(params.get("operation_id") or "").strip()
        if not operation_id:
            raise PaymentProviderValidationError("YooMoney operation_id is missing")
        attempt = db.scalar(
            select(PaymentProviderAttempt).where(
                PaymentProviderAttempt.checkout_id == checkout.id,
                PaymentProviderAttempt.provider == "yoomoney",
            )
        )
        if attempt is None:
            raise PaymentProviderValidationError("YooMoney payment attempt is unknown")
        row, _ = ingest_payment(db, {
            "provider": "yoomoney",
            "provider_event_id": operation_id,
            "idempotency_key": f"yoomoney:{operation_id}",
            "kind": "payment",
            "amount_minor": int(checkout.amount_minor),
            "currency": "RUB",
            "user_id": checkout.user_id,
            "organization_id": None,
            "metadata": {"checkout_id": checkout.id, "notification_type": params.get("notification_type", "")},
        })
        apply_payment_record(db, row, request.app.state.settings)
        attempt.provider_payment_id = operation_id
        attempt.status = "succeeded"
        db.commit()
    except (UnicodeDecodeError, ValueError, PaymentProviderValidationError, PaymentProviderUnavailable, BillingValidationError, BillingConflictError, BillingNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlainTextResponse("OK", status_code=200)


@router.post("/payments/yookassa/webhook", include_in_schema=False)
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    _bounded_content_length(request)
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payment webhook body is too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid YooKassa webhook JSON") from exc
    event = str(payload.get("event") or "").strip()
    obj = payload.get("object") or {}
    payment_id = str(obj.get("id") or "").strip()
    if event not in {"payment.succeeded", "payment.canceled", "payment.waiting_for_capture"} or not payment_id:
        return {"status": "ignored"}
    attempt = db.scalar(select(PaymentProviderAttempt).where(PaymentProviderAttempt.provider == "yookassa", PaymentProviderAttempt.provider_payment_id == payment_id))
    if attempt is None:
        raise HTTPException(status_code=404, detail="YooKassa payment attempt is unknown")
    checkout = db.get(BillingCheckout, attempt.checkout_id)
    if checkout is None:
        raise HTTPException(status_code=404, detail="Billing checkout is unknown")
    try:
        verified = await verify_yookassa_payment(db, request.app.state.settings, payment_id)
        verified_status = validate_yookassa_against_checkout(verified, checkout)
        if event == "payment.succeeded":
            if verified_status != "succeeded" or not bool(verified.get("paid")):
                raise PaymentProviderValidationError("YooKassa payment is not actually succeeded")
            row, _ = ingest_payment(db, {
                "provider": "yookassa",
                "provider_event_id": f"payment.succeeded:{payment_id}",
                "idempotency_key": f"yookassa:{payment_id}:succeeded",
                "kind": "payment",
                "amount_minor": int(checkout.amount_minor),
                "currency": checkout.currency.upper(),
                "user_id": checkout.user_id,
                "organization_id": None,
                "metadata": {"checkout_id": checkout.id, "payment_id": payment_id},
            })
            apply_payment_record(db, row, request.app.state.settings)
            attempt.status = "succeeded"
        elif event == "payment.canceled":
            if verified_status != "canceled":
                raise PaymentProviderValidationError("YooKassa payment is not actually canceled")
            attempt.status = "canceled"
            if checkout.status == "pending":
                checkout.status = "canceled"
                checkout.updated_at = datetime.now(timezone.utc)
        else:
            # We use capture=true; waiting_for_capture is not a success state and
            # never grants entitlement. Keep it observable only.
            attempt.status = verified_status
        db.commit()
    except (PaymentProviderUnavailable, PaymentProviderValidationError, BillingValidationError, BillingConflictError, BillingNotFoundError, ValueError) as exc:
        db.rollback()
        raise _provider_error(exc) from exc
    return {"status": "ok"}
