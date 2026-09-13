from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import AuthSession, User
from app.services.auth import token_digest

ADMIN_BROWSER_COOKIE = "x1_admin_session"


def _is_https(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or request.url.scheme.lower() == "https"


def set_admin_browser_session(response: Response, request: Request, *, token: str, is_admin: bool) -> None:
    """Maintain a server-readable browser gate for /admin without replacing API bearer auth."""
    if not is_admin:
        clear_admin_browser_session(response)
        return
    settings = getattr(request.app.state, "settings", get_settings())
    max_age = max(300, int(settings.session_ttl_days) * 24 * 60 * 60)
    response.set_cookie(
        ADMIN_BROWSER_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=_is_https(request),
        samesite="strict",
        path="/admin",
    )


def clear_admin_browser_session(response: Response) -> None:
    response.delete_cookie(ADMIN_BROWSER_COOKIE, path="/admin", httponly=True, samesite="strict")


def _redirect_to_login(request: Request) -> None:
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    location = "/admin/login?next=" + quote(target, safe="/")
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="Administrator sign-in required",
        headers={"Location": location, "Cache-Control": "no-store"},
    )


def require_admin_browser_session(request: Request, db: Session = Depends(get_db)) -> User:
    """Server-side guard for owner/admin HTML pages.

    Admin APIs continue to require the normal Authorization bearer token. This cookie is
    deliberately scoped to /admin and is not accepted by API authentication.
    """
    raw_token = str(request.cookies.get(ADMIN_BROWSER_COOKIE) or "").strip()
    if not raw_token:
        _redirect_to_login(request)

    now = datetime.now(timezone.utc)
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw_token)))
    if session is None or session.revoked_at is not None:
        _redirect_to_login(request)

    expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        _redirect_to_login(request)

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        _redirect_to_login(request)
    if not bool(user.is_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")

    last_seen = session.last_seen_at if session.last_seen_at.tzinfo else session.last_seen_at.replace(tzinfo=timezone.utc)
    if (now - last_seen).total_seconds() > 300:
        session.last_seen_at = now
        db.commit()
    request.state.admin_browser_session_id = session.id
    return user
