from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, ApiRateLimitWindow, Organization, OrganizationMember, User, utcnow
from app.services.auth import token_digest


API_TOKEN_RE = re.compile(r"^x1k_[0-9a-f]{8}_[A-Za-z0-9_-]{40,80}$")


def _extract_api_token(x_api_key: str, authorization: str) -> str:
    direct = x_api_key.strip()
    authorization = authorization.strip()
    if direct and authorization:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide an API key through exactly one credential header",
        )
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    token = direct or bearer
    if not API_TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")
    return token


def _minute_start(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def _consume_rate_limit(db: Session, api_key: ApiKey, now: datetime) -> dict[str, int]:
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
        retry_after = max(1, 60 - now.second)
        raise HTTPException(
            status_code=429,
            detail="API key rate limit exceeded",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(api_key.rate_limit_per_minute),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int((window_start + timedelta(minutes=1)).timestamp())),
            },
        )
    db.flush()
    consumed = int(
        db.scalar(select(ApiRateLimitWindow.request_count).where(ApiRateLimitWindow.id == window.id))
        or 0
    )
    return {
        "limit": int(api_key.rate_limit_per_minute),
        "remaining": max(0, int(api_key.rate_limit_per_minute) - consumed),
        "reset": int((window_start + timedelta(minutes=1)).timestamp()),
    }


def _organization_access_is_current(db: Session, api_key: ApiKey, user: User) -> bool:
    if api_key.organization_id is None:
        return True
    organization = db.get(Organization, api_key.organization_id)
    if organization is None:
        return False
    if organization.owner_id == user.id:
        return True
    return db.scalar(
        select(OrganizationMember.id).where(
            OrganizationMember.organization_id == organization.id,
            OrganizationMember.user_id == user.id,
            OrganizationMember.role.in_(("manager", "owner")),
        ).limit(1)
    ) is not None


def _authenticate_api_key_identity(db: Session, token: str, scope: str) -> tuple[ApiKey, User]:
    if not API_TOKEN_RE.fullmatch(token):
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
    if not _organization_access_is_current(db, row, user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key organization access revoked")
    return row, user


def authenticate_api_key(db: Session, token: str, scope: str) -> tuple[ApiKey, User]:
    now = datetime.now(timezone.utc)
    row, user = _authenticate_api_key_identity(db, token, scope)
    _consume_rate_limit(db, row, now)
    row.last_used_at = utcnow()
    db.commit()
    return row, user


def require_api_scope(scope: str):
    def dependency(
        request: Request,
        response: Response,
        x_api_key: str = Header(default="", alias="X-API-Key"),
        authorization: str = Header(default="", alias="Authorization"),
        db: Session = Depends(get_db),
    ) -> tuple[ApiKey, User]:
        token = _extract_api_token(x_api_key, authorization)
        key, user = _authenticate_api_key_identity(db, token, scope)
        rate = _consume_rate_limit(db, key, datetime.now(timezone.utc))
        key.last_used_at = utcnow()
        db.commit()
        request.state.api_key_id = key.id
        request.state.api_rate_limit = rate
        response.headers["X-RateLimit-Limit"] = str(rate["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rate["remaining"])
        response.headers["X-RateLimit-Reset"] = str(rate["reset"])
        return key, user

    return dependency
