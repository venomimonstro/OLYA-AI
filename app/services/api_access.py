from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, ApiRateLimitWindow, User, utcnow
from app.services.auth import token_digest


def _extract_api_token(x_api_key: str, authorization: str) -> str:
    if x_api_key.strip():
        return x_api_key.strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _minute_start(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def _consume_rate_limit(db: Session, api_key: ApiKey, now: datetime) -> None:
    """Atomically consume one request from a bounded fixed-minute API window."""
    window_start = _minute_start(now)
    window = db.scalar(
        select(ApiRateLimitWindow).where(
            ApiRateLimitWindow.api_key_id == api_key.id,
            ApiRateLimitWindow.window_start == window_start,
        )
    )
    created = False
    if window is None:
        candidate = ApiRateLimitWindow(
            api_key_id=api_key.id,
            window_start=window_start,
            request_count=0,
        )
        try:
            # A savepoint contains the unique-window race. A competing request
            # must not force rollback of unrelated state already present in the
            # request Session.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            window = candidate
            created = True
        except IntegrityError:
            window = db.scalar(
                select(ApiRateLimitWindow).where(
                    ApiRateLimitWindow.api_key_id == api_key.id,
                    ApiRateLimitWindow.window_start == window_start,
                )
            )
    if window is None:
        raise HTTPException(status_code=503, detail="API rate limiter unavailable")

    if created:
        # Keep a short overlap so an in-flight request that started just before a
        # minute boundary is never racing a DELETE of the row it is updating.
        # In steady state this bounds the table to only a few rows per API key.
        db.execute(
            delete(ApiRateLimitWindow).where(
                ApiRateLimitWindow.api_key_id == api_key.id,
                ApiRateLimitWindow.window_start < window_start - timedelta(minutes=2),
            )
        )

    result = db.execute(
        update(ApiRateLimitWindow)
        .where(
            ApiRateLimitWindow.id == window.id,
            ApiRateLimitWindow.request_count < api_key.rate_limit_per_minute,
        )
        .values(request_count=ApiRateLimitWindow.request_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="API key rate limit exceeded",
            headers={"Retry-After": "60"},
        )


def authenticate_api_key(db: Session, token: str, scope: str) -> tuple[ApiKey, User]:
    if not token.startswith("x1k_"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")
    row = db.scalar(select(ApiKey).where(ApiKey.secret_hash == token_digest(token)))
    now = datetime.now(timezone.utc)
    if row is None or row.status != "active" or row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    expires = row.expires_at
    if expires is not None:
        expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")
    if scope not in set(row.scopes or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"API key scope required: {scope}")
    user = db.get(User, row.owner_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key owner unavailable")

    _consume_rate_limit(db, row, now)
    row.last_used_at = utcnow()
    db.commit()
    return row, user


def require_api_scope(scope: str):
    def dependency(
        request: Request,
        x_api_key: str = Header(default="", alias="X-API-Key"),
        authorization: str = Header(default="", alias="Authorization"),
        db: Session = Depends(get_db),
    ) -> tuple[ApiKey, User]:
        token = _extract_api_token(x_api_key, authorization)
        key, user = authenticate_api_key(db, token, scope)
        request.state.api_key_id = key.id
        return key, user

    return dependency
