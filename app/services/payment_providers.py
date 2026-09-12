from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BillingCheckout, OwnerIntegrationSettings, PaymentProviderAttempt
from app.services.owner_integrations import decrypt_secret, get_owner_settings, integration_snapshot

YOOKASSA_API = "https://api.yookassa.ru/v3"
YOOMONEY_CONFIRM_URL = "https://yoomoney.ru/quickpay/confirm"
SUPPORTED_PROVIDERS = ("yookassa", "yoomoney")


class PaymentProviderError(RuntimeError):
    pass


class PaymentProviderUnavailable(PaymentProviderError):
    pass


class PaymentProviderValidationError(PaymentProviderError):
    pass


def minor_to_decimal(amount_minor: int) -> str:
    return format((Decimal(int(amount_minor)) / Decimal(100)).quantize(Decimal("0.01")), "f")


def decimal_to_minor(value: str) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise PaymentProviderValidationError("Invalid payment amount") from exc
    if amount < 0:
        raise PaymentProviderValidationError("Invalid payment amount")
    return int(amount * 100)


def provider_readiness(db: Session, settings) -> dict:
    snap = integration_snapshot(db, settings)
    payments = snap.get("payments") or {}
    return {
        "default_provider": payments.get("default_provider") or "yookassa",
        "providers": {
            name: {
                "enabled": bool((payments.get(name) or {}).get("enabled")),
                "ready": bool((payments.get(name) or {}).get("ready")),
            }
            for name in SUPPORTED_PROVIDERS
        },
    }


def require_provider(db: Session, settings, provider: str) -> tuple[str, OwnerIntegrationSettings]:
    name = str(provider or "").strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise PaymentProviderValidationError("Unsupported payment provider")
    readiness = provider_readiness(db, settings)
    if not readiness["providers"].get(name, {}).get("ready"):
        raise PaymentProviderUnavailable(f"Payment provider {name} is not ready")
    row = get_owner_settings(db)
    if row is None:
        raise PaymentProviderUnavailable("Payment integrations are not configured")
    return name, row


def get_or_create_attempt(db: Session, checkout: BillingCheckout, provider: str) -> PaymentProviderAttempt:
    row = db.scalar(
        select(PaymentProviderAttempt).where(
            PaymentProviderAttempt.checkout_id == checkout.id,
            PaymentProviderAttempt.provider == provider,
        )
    )
    if row is not None:
        return row
    candidate = PaymentProviderAttempt(
        checkout_id=checkout.id,
        provider=provider,
        status="pending",
        metadata_json={},
    )
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate
    except IntegrityError:
        winner = db.scalar(
            select(PaymentProviderAttempt).where(
                PaymentProviderAttempt.checkout_id == checkout.id,
                PaymentProviderAttempt.provider == provider,
            )
        )
        if winner is None:
            raise
        return winner


def yoomoney_form(db: Session, settings, checkout: BillingCheckout, attempt: PaymentProviderAttempt) -> dict[str, str]:
    _, owner = require_provider(db, settings, "yoomoney")
    if attempt.checkout_id != checkout.id or attempt.provider != "yoomoney":
        raise PaymentProviderValidationError("Payment attempt mismatch")
    base = owner.public_base_url.rstrip("/")
    label = f"x1:{checkout.id}"
    fields = {
        "receiver": owner.yoomoney_receiver,
        "quickpay-form": "button",
        "paymentType": "AC",
        "sum": minor_to_decimal(checkout.amount_minor),
        "label": label,
        "successURL": f"{base}/app?payment=return&checkout={checkout.id}",
    }
    attempt.metadata_json = {"label": label, "expected_amount_minor": int(checkout.amount_minor)}
    return fields


def verify_yoomoney_notification(db: Session, settings, params: dict[str, str]) -> BillingCheckout:
    _, owner = require_provider(db, settings, "yoomoney")
    supplied = str(params.get("sign") or "").strip().lower()
    if not supplied:
        raise PaymentProviderValidationError("YooMoney notification signature is missing")
    canonical_pairs = sorted((str(k), str(v)) for k, v in params.items() if k != "sign")
    canonical = urlencode(canonical_pairs)
    secret = decrypt_secret(settings, owner.yoomoney_notification_secret_ciphertext).encode("utf-8")
    expected = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise PaymentProviderValidationError("YooMoney notification signature is invalid")
    if str(params.get("unaccepted") or "false").lower() not in {"false", "0", ""}:
        raise PaymentProviderValidationError("YooMoney payment is not accepted")
    if str(params.get("test_notification") or "false").lower() in {"true", "1"}:
        raise PaymentProviderValidationError("YooMoney test notification cannot settle billing")
    label = str(params.get("label") or "")
    if not label.startswith("x1:"):
        raise PaymentProviderValidationError("YooMoney label is invalid")
    checkout = db.get(BillingCheckout, label[3:])
    if checkout is None:
        raise PaymentProviderValidationError("YooMoney checkout is unknown")
    if str(params.get("currency") or "") != "643":
        raise PaymentProviderValidationError("YooMoney currency must be RUB (643)")
    # withdraw_amount is the amount withdrawn from the payer. It is the only
    # value that can be compared with our server-owned checkout price without
    # accidentally accepting a commission-reduced amount received by wallet.
    if decimal_to_minor(str(params.get("withdraw_amount") or "")) != int(checkout.amount_minor):
        raise PaymentProviderValidationError("YooMoney amount does not match checkout")
    return checkout


async def create_yookassa_payment(db: Session, settings, checkout: BillingCheckout, attempt: PaymentProviderAttempt) -> dict:
    _, owner = require_provider(db, settings, "yookassa")
    if attempt.checkout_id != checkout.id or attempt.provider != "yookassa":
        raise PaymentProviderValidationError("Payment attempt mismatch")
    if attempt.provider_payment_id and attempt.confirmation_url:
        return {
            "id": attempt.provider_payment_id,
            "status": attempt.status,
            "confirmation_url": attempt.confirmation_url,
        }
    secret = decrypt_secret(settings, owner.yookassa_secret_ciphertext)
    payload = {
        "amount": {"value": minor_to_decimal(checkout.amount_minor), "currency": checkout.currency.upper()},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{owner.public_base_url.rstrip('/')}/app?payment=return&checkout={checkout.id}",
        },
        "description": f"X1 AI · {checkout.plan.upper()} · 30 days"[:128],
        "metadata": {"checkout_id": checkout.id, "user_id": checkout.user_id, "plan": checkout.plan},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.post(
                f"{YOOKASSA_API}/payments",
                auth=(owner.yookassa_shop_id, secret),
                headers={"Idempotence-Key": f"x1-{checkout.id}-yookassa", "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PaymentProviderUnavailable("YooKassa payment creation failed") from exc
    payment_id = str(data.get("id") or "").strip()
    status = str(data.get("status") or "pending").strip().lower()
    confirmation_url = str((data.get("confirmation") or {}).get("confirmation_url") or "").strip()
    if not payment_id or not confirmation_url.startswith("https://"):
        raise PaymentProviderUnavailable("YooKassa did not return a safe confirmation URL")
    attempt.provider_payment_id = payment_id
    attempt.status = status
    attempt.confirmation_url = confirmation_url
    attempt.metadata_json = {"expected_amount_minor": int(checkout.amount_minor)}
    db.flush()
    return {"id": payment_id, "status": status, "confirmation_url": confirmation_url}


async def verify_yookassa_payment(db: Session, settings, payment_id: str) -> dict:
    _, owner = require_provider(db, settings, "yookassa")
    secret = decrypt_secret(settings, owner.yookassa_secret_ciphertext)
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.get(
                f"{YOOKASSA_API}/payments/{payment_id}",
                auth=(owner.yookassa_shop_id, secret),
                headers={"Accept": "application/json"},
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PaymentProviderUnavailable("YooKassa payment verification failed") from exc
    if str(data.get("id") or "") != payment_id:
        raise PaymentProviderValidationError("YooKassa payment identity mismatch")
    return data


def validate_yookassa_against_checkout(data: dict, checkout: BillingCheckout) -> str:
    metadata = data.get("metadata") or {}
    amount = data.get("amount") or {}
    if str(metadata.get("checkout_id") or "") != checkout.id:
        raise PaymentProviderValidationError("YooKassa checkout metadata mismatch")
    if str(metadata.get("user_id") or "") != checkout.user_id:
        raise PaymentProviderValidationError("YooKassa user metadata mismatch")
    if str(amount.get("currency") or "").upper() != checkout.currency.upper():
        raise PaymentProviderValidationError("YooKassa currency mismatch")
    if decimal_to_minor(str(amount.get("value") or "")) != int(checkout.amount_minor):
        raise PaymentProviderValidationError("YooKassa amount mismatch")
    return str(data.get("status") or "pending").strip().lower()
