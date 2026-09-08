from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
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
    max_active = max(1, min(100, int(settings.session_max_active_per_user)))

    # Serialize session creation for one account on PostgreSQL. Without this,
    # concurrent login bursts can all observe the old active-session set and
    # temporarily bypass the cap. SQLite test databases safely ignore FOR UPDATE.
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    record = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=now + timedelta(days=settings.session_ttl_days),
        last_seen_at=now,
    )
    db.add(record)
    db.flush()

    keep_ids = list(
        db.scalars(
            select(AuthSession.id)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc(), AuthSession.id.desc())
            .limit(max_active)
        ).all()
    )
    if keep_ids:
        db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.id.not_in(keep_ids),
            )
            .values(revoked_at=now)
        )
    db.commit()
    db.refresh(record)
    return token, record


def _expensive_public_request(request: Request) -> bool:
    """Identify public operations that can consume scarce CPU/RAM/disk/network.

    Project CRUD remains available so a newly registered account can enter the
    product, but compute/storage-heavy development surfaces stay behind the same
    progressive rollout gate as chat and research. File upload is special-cased
    because its URL lives under otherwise-cheap project CRUD.
    """
    path = request.url.path.rstrip("/")
    if path.startswith(
        (
            "/v1/chat",
            "/v1/images",
            "/v1/documents",
            "/v1/research",
            "/v1/project-sandboxes",
            "/v1/development",
            "/v1/engineering",
            "/v1/execution",
            "/v1/code",
            "/v1/project-runtimes",
            "/v1/git",
        )
    ):
        return True
    if request.method.upper() == "POST":
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "files":
            return True
    return False


def _enforce_public_exposure(request: Request, db: Session, user: User) -> None:
    settings = getattr(request.app.state, "settings", get_settings())
    if not _expensive_public_request(request) or bool(getattr(user, "is_admin", False)):
        return
    from app.services.progressive_launch import user_has_open_breaker
    if user_has_open_breaker(db, user.id):
        raise HTTPException(status_code=429, detail={"code": "user_circuit_breaker_open", "message": "AI access is temporarily paused for this account because an abuse/resource safety guardrail was triggered."})
    if not bool(getattr(settings, "public_launch_enforce_exposure", False)):
        return
    from app.models import BetaParticipant
    from app.services.progressive_launch import active_rollout, rollout_allows_user
    beta = db.scalar(select(BetaParticipant.id).where(BetaParticipant.user_id == user.id, BetaParticipant.state != "removed").limit(1))
    rollout = active_rollout(db)
    if beta or rollout_allows_user(rollout, user.id):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "public_rollout_not_exposed", "message": "AI access for this account has not been enabled by the current rollout stage yet.", "exposure_percent": rollout.exposure_percent if rollout else 0})


def _enforce_resource_lane(request: Request, db: Session, user: User) -> None:
    if request.method.upper() != "POST":
        return
    path = request.url.path
    channel = None
    if path == "/v1/images/generations":
        channel = "image_worker"
    elif path.startswith("/v1/project-sandboxes/") and path.endswith("/execute"):
        channel = "sandbox"
    elif path == "/v1/project-sandboxes/previews":
        channel = "sandbox"
    if not channel:
        return
    from app.services.measured_plans import ensure_channel_budget
    try:
        ensure_channel_budget(db, user, request.app.state.settings, channel, 0)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    now = datetime.now(timezone.utc)
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(credentials.credentials)))
    if session is None or session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    _enforce_public_exposure(request, db, user)
    _enforce_resource_lane(request, db, user)
    last_seen = session.last_seen_at if session.last_seen_at.tzinfo else session.last_seen_at.replace(tzinfo=timezone.utc)
    if (now - last_seen).total_seconds() > 300:
        session.last_seen_at = now
        db.commit()
    request.state.auth_session_id = session.id
    return user
