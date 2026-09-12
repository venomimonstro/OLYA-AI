from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ExternalAuthIdentity, OAuthLoginState, User
from app.services.auth import hash_password, normalize_email
from app.services.owner_integrations import decrypt_secret, encrypt_secret, ensure_email_state, get_owner_settings, integration_snapshot

YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USERINFO_URL = "https://login.yandex.ru/info"
YANDEX_PROVIDER = "yandex"
YANDEX_SCOPES = "login:info login:email"
STATE_TTL_MINUTES = 10


class YandexOAuthError(RuntimeError):
    pass


class YandexOAuthUnavailable(YandexOAuthError):
    pass


class YandexOAuthValidationError(YandexOAuthError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _settings_row(db: Session, settings):
    snap = integration_snapshot(db, settings)
    yandex = (snap.get("auth") or {}).get("yandex") or {}
    if not yandex.get("ready"):
        raise YandexOAuthUnavailable("Yandex login is disabled or not fully configured")
    row = get_owner_settings(db)
    if row is None:
        raise YandexOAuthUnavailable("Yandex login settings are unavailable")
    return row, yandex


@dataclass(frozen=True)
class YandexAuthorization:
    url: str
    state: str


def create_authorization(db: Session, settings) -> YandexAuthorization:
    row, yandex = _settings_row(db, settings)
    now = _now()
    db.execute(delete(OAuthLoginState).where(OAuthLoginState.expires_at < now - timedelta(hours=1)))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    login_state = OAuthLoginState(
        provider=YANDEX_PROVIDER,
        state_hash=_digest(state),
        pkce_verifier_ciphertext=encrypt_secret(settings, verifier),
        expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
    )
    db.add(login_state)
    db.commit()
    params = {
        "response_type": "code",
        "client_id": row.yandex_oauth_client_id,
        "redirect_uri": yandex["redirect_uri"],
        "scope": YANDEX_SCOPES,
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    return YandexAuthorization(url=f"{YANDEX_AUTHORIZE_URL}?{urlencode(params)}", state=state)


def consume_state(db: Session, settings, *, state: str, cookie_state: str) -> str:
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise YandexOAuthValidationError("OAuth state mismatch")
    now = _now()
    row = db.scalar(
        select(OAuthLoginState)
        .where(OAuthLoginState.provider == YANDEX_PROVIDER, OAuthLoginState.state_hash == _digest(state))
        .with_for_update()
    )
    if row is None or row.used_at is not None or (_aware(row.expires_at) or now) <= now:
        raise YandexOAuthValidationError("OAuth state is invalid, expired, or already used")
    row.used_at = now
    verifier = decrypt_secret(settings, row.pkce_verifier_ciphertext)
    if len(verifier) < 43:
        raise YandexOAuthValidationError("OAuth PKCE verifier is invalid")
    db.flush()
    return verifier


async def exchange_code(db: Session, settings, *, code: str, verifier: str) -> dict:
    owner, _ = _settings_row(db, settings)
    secret = decrypt_secret(settings, owner.yandex_oauth_client_secret_ciphertext)
    if not code or len(code) > 2048:
        raise YandexOAuthValidationError("Yandex authorization code is invalid")
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.post(
                YANDEX_TOKEN_URL,
                auth=(owner.yandex_oauth_client_id, secret),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                },
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise YandexOAuthUnavailable("Yandex token exchange failed") from exc
    access_token = str(data.get("access_token") or "").strip()
    token_type = str(data.get("token_type") or "bearer").lower()
    if not access_token or token_type != "bearer":
        raise YandexOAuthValidationError("Yandex did not return a usable bearer token")
    return {"access_token": access_token}


async def fetch_profile(db: Session, settings, *, access_token: str) -> dict:
    owner, _ = _settings_row(db, settings)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(
                YANDEX_USERINFO_URL,
                params={"format": "json"},
                headers={"Authorization": f"OAuth {access_token}", "Accept": "application/json"},
            )
        response.raise_for_status()
        profile = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise YandexOAuthUnavailable("Yandex user profile request failed") from exc
    subject = str(profile.get("id") or "").strip()
    email = normalize_email(str(profile.get("default_email") or ""))
    client_id = str(profile.get("client_id") or "").strip()
    if not subject or not email or "@" not in email:
        raise YandexOAuthValidationError("Yandex profile does not contain the required identity and email")
    if not client_id or not secrets.compare_digest(client_id, owner.yandex_oauth_client_id):
        raise YandexOAuthValidationError("Yandex token was issued for another OAuth application")
    display_name = str(profile.get("display_name") or profile.get("real_name") or profile.get("login") or "").strip()[:120]
    return {"subject": subject[:160], "email": email[:320], "display_name": display_name}


def resolve_user(db: Session, *, subject: str, email: str, display_name: str) -> User:
    identity = db.scalar(select(ExternalAuthIdentity).where(ExternalAuthIdentity.provider == YANDEX_PROVIDER, ExternalAuthIdentity.subject == subject))
    now = _now()
    if identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise YandexOAuthValidationError("Linked X1 account is unavailable")
        identity.last_login_at = now
        identity.email_at_link = email
        ensure_email_state(db, user, mark_verified=True)
        return user

    user = db.scalar(select(User).where(User.email == email))
    if user is not None and not user.is_active:
        raise YandexOAuthValidationError("X1 account with this email is unavailable")
    if user is None:
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(64)), display_name=display_name)
        db.add(user)
        db.flush()
    elif display_name and not str(user.display_name or "").strip():
        user.display_name = display_name

    ensure_email_state(db, user, mark_verified=True)
    identity = ExternalAuthIdentity(user_id=user.id, provider=YANDEX_PROVIDER, subject=subject, email_at_link=email, last_login_at=now)
    try:
        with db.begin_nested():
            db.add(identity)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalar(select(ExternalAuthIdentity).where(ExternalAuthIdentity.provider == YANDEX_PROVIDER, ExternalAuthIdentity.subject == subject))
        if winner is None or winner.user_id != user.id:
            raise YandexOAuthValidationError("Yandex identity is already linked to another account") from exc
    return user
