from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthSession, User, UserEmailState
from app.schemas.auth import AuthResponse, LoginRequest, MeResponse, PasswordResetConfirm, PasswordResetRequest, RegisterRequest, VerifyEmailRequest
from app.services.auth import create_session, get_current_user, hash_password, normalize_email, verify_password
from app.services.auth_rate_limit import enforce_auth_rate_limit
from app.services.owner_integrations import (
    consume_email_token,
    email_verification_required_for_user,
    ensure_email_state,
    get_owner_settings,
    integration_snapshot,
    send_password_reset_email,
    send_verification_email,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Non-existent accounts still execute one real scrypt verification. This removes
# the large timing gap that would otherwise help remote account enumeration.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def _verification_required(db: Session) -> bool:
    row = get_owner_settings(db)
    return bool(row and row.auth_email_verification_required)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
    email = normalize_email(payload.email)
    enforce_auth_rate_limit(request, email=email, action="register", environment=request.app.state.settings.env)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")

    verification_required = _verification_required(db)
    if verification_required and not integration_snapshot(db, request.app.state.settings).get("smtp", {}).get("ready"):
        raise HTTPException(status_code=503, detail="Email registration is temporarily unavailable because SMTP is not ready")

    user = User(email=email, password_hash=hash_password(payload.password), display_name=payload.display_name.strip())
    db.add(user)
    try:
        db.flush()
        ensure_email_state(db, user, mark_verified=not verification_required)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists") from exc
    db.refresh(user)

    delivery_ok = True
    if verification_required:
        try:
            send_verification_email(db, request.app.state.settings, user)
            db.commit()
        except Exception:
            db.rollback()
            delivery_ok = False

    token, _ = create_session(db, user)
    if verification_required and not delivery_ok:
        # Account creation succeeded, but make the recoverable state explicit;
        # the user can request a resend without creating a duplicate account.
        raise HTTPException(
            status_code=503,
            detail={"code": "verification_delivery_failed", "message": "Account created, but the verification email could not be sent. Try resend shortly."},
        )
    return AuthResponse(access_token=token, user_id=user.id, verification_required=verification_required)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
    email = normalize_email(payload.email)
    enforce_auth_rate_limit(request, email=email, action="login", environment=request.app.state.settings.env)
    user = db.scalar(select(User).where(User.email == email))
    candidate_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, candidate_hash)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if email_verification_required_for_user(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "email_verification_required", "message": "Confirm your email address before signing in."},
        )
    token, _ = create_session(db, user)
    return AuthResponse(access_token=token, user_id=user.id, verification_required=False)


@router.post("/verification/resend", status_code=status.HTTP_204_NO_CONTENT)
def resend_verification(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> None:
    email = normalize_email(payload.email)
    enforce_auth_rate_limit(request, email=email, action="verification_resend", environment=request.app.state.settings.env)
    snap = integration_snapshot(db, request.app.state.settings)
    if not snap.get("smtp", {}).get("ready"):
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return
    state = db.get(UserEmailState, user.id)
    if state is not None and state.verified_at is not None:
        return
    try:
        send_verification_email(db, request.app.state.settings, user)
        db.commit()
    except Exception:
        db.rollback()
        # Preserve account-enumeration resistance: delivery errors are not tied
        # to whether the submitted address exists.
        return


@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)) -> dict:
    try:
        user = consume_email_token(db, raw_token=payload.token, purpose="verify_email")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = ensure_email_state(db, user)
    state.verified_at = datetime.now(timezone.utc)
    state.updated_at = state.verified_at
    db.commit()
    return {"status": "verified", "email": user.email}


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_request(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> None:
    email = normalize_email(payload.email)
    enforce_auth_rate_limit(request, email=email, action="password_reset", environment=request.app.state.settings.env)
    if not integration_snapshot(db, request.app.state.settings).get("smtp", {}).get("ready"):
        raise HTTPException(status_code=503, detail="Password recovery email is temporarily unavailable")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return
    try:
        send_password_reset_email(db, request.app.state.settings, user)
        db.commit()
    except Exception:
        db.rollback()
        return


@router.post("/password-reset/confirm")
def password_reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)) -> dict:
    try:
        user = consume_email_token(db, raw_token=payload.token, purpose="password_reset")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user.password_hash = hash_password(payload.new_password)
    ensure_email_state(db, user, mark_verified=True)
    now = datetime.now(timezone.utc)
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()
    return {"status": "password_updated"}


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeResponse:
    state = db.get(UserEmailState, user.id)
    verified = True if state is None else state.verified_at is not None
    return MeResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin, email_verified=verified)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id:
        session = db.get(AuthSession, session_id)
        if session and session.user_id == user.id and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    now = datetime.now(timezone.utc)
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()
