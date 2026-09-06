from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import AuthSession, User


_password_scheme = "scrypt-v1"
_bearer = HTTPBearer(auto_error=False)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"{_password_scheme}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = encoded.split("$", 2)
        if scheme != _password_scheme:
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(hash_b64.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, AuthSession]:
    settings = get_settings()
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    record = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=now + timedelta(days=settings.session_ttl_days),
        last_seen_at=now,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return token, record


def _expensive_public_path(path: str) -> bool:
    return path.startswith((
        "/v1/chat",
        "/v1/images",
        "/v1/documents",
        "/v1/research",
        "/v1/project-sandboxes",
        "/v1/development",
        "/v1/engineering",
        "/v1/execution",
    ))


def _enforce_public_exposure(request: Request, db: Session, user: User) -> None:
    settings = getattr(request.app.state, "settings", get_settings())
    if not bool(getattr(settings, "public_launch_enforce_exposure", False)) or not _expensive_public_path(request.url.path):
        return
    if bool(getattr(user, "is_admin", False)):
        return
    from app.models import BetaParticipant
    from app.services.progressive_launch import active_rollout, rollout_allows_user

    beta = db.scalar(
        select(BetaParticipant.id)
        .where(BetaParticipant.user_id == user.id, BetaParticipant.state != "removed")
        .limit(1)
    )
    if beta:
        return
    rollout = active_rollout(db)
    if rollout_allows_user(rollout, user.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "public_rollout_not_exposed",
            "message": "AI access for this account has not been enabled by the current rollout stage yet.",
            "exposure_percent": rollout.exposure_percent if rollout else 0,
        },
    )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    now = datetime.now(timezone.utc)
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(credentials.credentials)))
    if session is None or session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    _enforce_public_exposure(request, db, user)

    last_seen = session.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    if (now - last_seen).total_seconds() > 300:
        session.last_seen_at = now
        db.commit()

    request.state.auth_session_id = session.id
    return user
