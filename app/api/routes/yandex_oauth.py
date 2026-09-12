from __future__ import annotations

import html
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import create_session
from app.services.auth_rate_limit import enforce_auth_rate_limit
from app.services.owner_integrations import integration_snapshot
from app.services.yandex_oauth import (
    YandexOAuthError,
    YandexOAuthUnavailable,
    create_authorization,
    consume_state,
    exchange_code,
    fetch_profile,
    resolve_user,
)

router = APIRouter(prefix="/v1/auth", tags=["auth-yandex"])
COOKIE_NAME = "x1_yandex_oauth_state"


def _is_prod(request: Request) -> bool:
    return str(request.app.state.settings.env).lower() in {"production", "prod", "stable"}


def _completion_page(*, token: str | None = None, user_id: str | None = None, is_admin: bool = False, error: str = "") -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    if token and user_id:
        token_js = json.dumps(token)
        user_js = json.dumps(user_id)
        script = (
            f"sessionStorage.setItem('x1_access_token',{token_js});"
            f"sessionStorage.setItem('x1_user_id',{user_js});"
            + (f"sessionStorage.setItem('x1AdminToken',{token_js});" if is_admin else "")
            + "location.replace('/login?oauth=yandex');"
        )
        title = "Вход выполнен"
        body = "Вход через Яндекс выполнен. Открываю X1…"
    else:
        script = ""
        title = "Не удалось войти"
        body = html.escape(error or "Не удалось выполнить вход через Яндекс. Вернитесь на страницу входа и повторите попытку.")
    page = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — X1 AI</title><style nonce="{nonce}">body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#090b10;color:#f4f6fa;font:16px/1.5 system-ui}}main{{width:min(520px,calc(100% - 28px));padding:26px;border:1px solid #29313d;border-radius:16px;background:#111620}}a{{color:#fff}}</style></head><body><main><h1>{title}</h1><p>{body}</p>{'<p><a href="/login">Вернуться ко входу</a></p>' if not token else ''}</main><script nonce="{nonce}">{script}</script></body></html>'''
    response = HTMLResponse(page, status_code=200 if token else 400)
    response.headers.update({
        "Content-Security-Policy": f"default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
    response.delete_cookie(COOKIE_NAME, path="/v1/auth/yandex")
    return response


@router.get("/providers")
def auth_providers(request: Request, db: Session = Depends(get_db)) -> dict:
    snap = integration_snapshot(db, request.app.state.settings)
    yandex = (snap.get("auth") or {}).get("yandex") or {}
    return {
        "yandex": {
            "enabled": bool(yandex.get("enabled")),
            "ready": bool(yandex.get("ready")),
            "start_url": "/v1/auth/yandex/start" if yandex.get("ready") else None,
        }
    }


@router.get("/yandex/start")
def yandex_start(request: Request, db: Session = Depends(get_db)):
    # OAuth has no email identity before the redirect. Use global + IP buckets;
    # never put unrelated users into one fabricated shared email bucket.
    enforce_auth_rate_limit(request, email="", action="yandex_oauth", environment=request.app.state.settings.env)
    try:
        authorization = create_authorization(db, request.app.state.settings)
    except YandexOAuthUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = RedirectResponse(authorization.url, status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        authorization.state,
        max_age=10 * 60,
        httponly=True,
        secure=_is_prod(request),
        samesite="lax",
        path="/v1/auth/yandex",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/yandex/callback", response_class=HTMLResponse)
async def yandex_callback(
    request: Request,
    code: str = Query(default="", max_length=2048),
    state: str = Query(default="", max_length=256),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if error:
        return _completion_page(error="Яндекс отменил или не подтвердил авторизацию.")
    cookie_state = request.cookies.get(COOKIE_NAME, "")
    try:
        verifier = consume_state(db, request.app.state.settings, state=state, cookie_state=cookie_state)
        db.commit()
        token_data = await exchange_code(db, request.app.state.settings, code=code, verifier=verifier)
        profile = await fetch_profile(db, request.app.state.settings, access_token=token_data["access_token"])
        user = resolve_user(db, subject=profile["subject"], email=profile["email"], display_name=profile["display_name"])
        db.commit()
        local_token, _ = create_session(db, user)
        return _completion_page(token=local_token, user_id=user.id, is_admin=bool(user.is_admin))
    except YandexOAuthError as exc:
        db.rollback()
        return _completion_page(error=str(exc))
    except Exception:
        db.rollback()
        return _completion_page(error="Вход через Яндекс временно недоступен. Повторите попытку позже.")
