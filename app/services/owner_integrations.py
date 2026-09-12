from __future__ import annotations

import base64
import hashlib
import smtplib
import ssl
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthEmailToken, OwnerIntegrationSettings, User, UserEmailState


GLOBAL_SETTINGS_ID = "global"
TOKEN_TTL_MINUTES = {"verify_email": 60 * 24, "password_reset": 45}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fernet(settings) -> Fernet:
    seed = str(getattr(settings, "project_runtime_secret_key", "") or "").encode("utf-8")
    if not seed or seed == b"change-me-runtime-secret":
        seed = str(getattr(settings, "admin_bootstrap_token", "") or "x1-development-secret").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(b"x1-owner-integrations-v1:" + seed).digest())
    return Fernet(key)


def encrypt_secret(settings, value: str) -> str:
    value = str(value or "")
    return "" if not value else _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(settings, value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    try:
        return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise RuntimeError("Stored integration secret cannot be decrypted with the current runtime secret") from exc


def get_owner_settings(db: Session, *, create: bool = False) -> OwnerIntegrationSettings | None:
    row = db.get(OwnerIntegrationSettings, GLOBAL_SETTINGS_ID)
    if row is None and create:
        row = OwnerIntegrationSettings(id=GLOBAL_SETTINGS_ID)
        db.add(row)
        db.flush()
    return row


def _base_url_valid(value: str, *, production: bool) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ({"https"} if production else {"http", "https"}):
        return False
    return bool(parsed.netloc and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)


def integration_snapshot(db: Session, settings) -> dict:
    row = get_owner_settings(db)
    if row is None:
        return {
            "configured": False,
            "public_base_url": "",
            "smtp": {"enabled": False, "ready": False, "password_set": False},
            "auth": {"yandex": {"enabled": False, "ready": False, "client_id": "", "secret_set": False, "redirect_uri": ""}},
            "metrika": {"enabled": False, "ready": False, "counter_id": "", "webvisor": False, "goal_catalog": metrika_goal_catalog()},
            "payments": {
                "default_provider": "yookassa",
                "yoomoney": {"enabled": False, "ready": False, "receiver": "", "secret_set": False},
                "yookassa": {"enabled": False, "ready": False, "shop_id": "", "secret_set": False},
            },
            "auth_email_verification_required": False,
        }
    production = str(getattr(settings, "env", "development")).lower() in {"production", "prod", "stable"}
    base_ready = _base_url_valid(row.public_base_url, production=production)
    smtp_password_set = bool(row.smtp_password_ciphertext)
    smtp_auth_ready = not row.smtp_username.strip() or smtp_password_set
    smtp_ready = bool(
        row.smtp_enabled
        and row.smtp_host.strip()
        and 1 <= int(row.smtp_port) <= 65535
        and row.smtp_from_email.strip()
        and smtp_auth_ready
        and not (row.smtp_use_tls and row.smtp_use_ssl)
    )
    yandex_secret_set = bool(row.yandex_oauth_client_secret_ciphertext)
    yandex_ready = bool(
        row.yandex_oauth_enabled
        and row.yandex_oauth_client_id.strip()
        and yandex_secret_set
        and base_ready
    )
    yoomoney_ready = bool(row.yoomoney_enabled and row.yoomoney_receiver.strip() and row.yoomoney_notification_secret_ciphertext and base_ready)
    yookassa_ready = bool(row.yookassa_enabled and row.yookassa_shop_id.strip() and row.yookassa_secret_ciphertext and base_ready)
    counter = str(row.metrika_counter_id or "").strip()
    redirect_uri = f"{row.public_base_url.rstrip('/')}/v1/auth/yandex/callback" if base_ready else ""
    return {
        "configured": True,
        "public_base_url": row.public_base_url,
        "public_base_url_ready": base_ready,
        "smtp": {
            "enabled": bool(row.smtp_enabled), "ready": smtp_ready, "host": row.smtp_host, "port": int(row.smtp_port),
            "username": row.smtp_username, "password_set": smtp_password_set, "from_email": row.smtp_from_email,
            "from_name": row.smtp_from_name, "use_tls": bool(row.smtp_use_tls), "use_ssl": bool(row.smtp_use_ssl),
        },
        "auth": {
            "yandex": {
                "enabled": bool(row.yandex_oauth_enabled), "ready": yandex_ready,
                "client_id": row.yandex_oauth_client_id, "secret_set": yandex_secret_set,
                "redirect_uri": redirect_uri,
            }
        },
        "metrika": {
            "enabled": bool(row.metrika_enabled),
            "ready": bool(row.metrika_enabled and counter.isdigit() and int(counter) > 0),
            "counter_id": counter, "webvisor": bool(row.metrika_webvisor), "goal_catalog": metrika_goal_catalog(),
        },
        "payments": {
            "default_provider": row.payment_default_provider,
            "yoomoney": {"enabled": bool(row.yoomoney_enabled), "ready": yoomoney_ready, "receiver": row.yoomoney_receiver, "secret_set": bool(row.yoomoney_notification_secret_ciphertext), "notification_path": "/v1/commerce/payments/yoomoney/notify"},
            "yookassa": {"enabled": bool(row.yookassa_enabled), "ready": yookassa_ready, "shop_id": row.yookassa_shop_id, "secret_set": bool(row.yookassa_secret_ciphertext), "webhook_path": "/v1/commerce/payments/yookassa/webhook"},
        },
        "auth_email_verification_required": bool(row.auth_email_verification_required),
    }


def update_owner_settings(db: Session, settings, actor_id: str, payload: dict) -> OwnerIntegrationSettings:
    row = get_owner_settings(db, create=True)
    assert row is not None
    simple_fields = {
        "public_base_url",
        "smtp_enabled", "smtp_host", "smtp_port", "smtp_username", "smtp_from_email", "smtp_from_name", "smtp_use_tls", "smtp_use_ssl",
        "auth_email_verification_required", "yandex_oauth_enabled", "yandex_oauth_client_id",
        "metrika_enabled", "metrika_counter_id", "metrika_webvisor",
        "yoomoney_enabled", "yoomoney_receiver", "yookassa_enabled", "yookassa_shop_id", "payment_default_provider",
    }
    for field in simple_fields:
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    if "smtp_password" in payload and payload.get("smtp_password") is not None:
        row.smtp_password_ciphertext = encrypt_secret(settings, str(payload.get("smtp_password") or ""))
    if "yandex_oauth_client_secret" in payload and payload.get("yandex_oauth_client_secret") is not None:
        row.yandex_oauth_client_secret_ciphertext = encrypt_secret(settings, str(payload.get("yandex_oauth_client_secret") or ""))
    if "yoomoney_notification_secret" in payload and payload.get("yoomoney_notification_secret") is not None:
        row.yoomoney_notification_secret_ciphertext = encrypt_secret(settings, str(payload.get("yoomoney_notification_secret") or ""))
    if "yookassa_secret" in payload and payload.get("yookassa_secret") is not None:
        row.yookassa_secret_ciphertext = encrypt_secret(settings, str(payload.get("yookassa_secret") or ""))

    row.public_base_url = str(row.public_base_url or "").strip().rstrip("/")
    row.smtp_host = str(row.smtp_host or "").strip()
    row.smtp_username = str(row.smtp_username or "").strip()
    row.smtp_from_email = str(row.smtp_from_email or "").strip().lower()
    row.smtp_from_name = str(row.smtp_from_name or "X1 AI").strip()[:160]
    row.yandex_oauth_client_id = str(row.yandex_oauth_client_id or "").strip()
    row.metrika_counter_id = str(row.metrika_counter_id or "").strip()
    row.yoomoney_receiver = str(row.yoomoney_receiver or "").strip()
    row.yookassa_shop_id = str(row.yookassa_shop_id or "").strip()
    row.payment_default_provider = str(row.payment_default_provider or "yookassa").strip().lower()

    if row.payment_default_provider not in {"yoomoney", "yookassa"}:
        raise ValueError("payment_default_provider must be yoomoney or yookassa")
    if not 1 <= int(row.smtp_port) <= 65535:
        raise ValueError("SMTP port must be between 1 and 65535")
    if row.smtp_use_tls and row.smtp_use_ssl:
        raise ValueError("Choose either SMTP STARTTLS or SMTP SSL, not both")
    production = str(getattr(settings, "env", "development")).lower() in {"production", "prod", "stable"}
    if row.public_base_url and not _base_url_valid(row.public_base_url, production=production):
        raise ValueError("Public base URL must be a clean HTTPS origin in production")
    if row.yandex_oauth_enabled:
        if not row.public_base_url or not _base_url_valid(row.public_base_url, production=production):
            raise ValueError("Yandex login requires a valid Public base URL")
        if len(row.yandex_oauth_client_id) < 8 or len(row.yandex_oauth_client_id) > 160:
            raise ValueError("Yandex OAuth Client ID is invalid")
        if not row.yandex_oauth_client_secret_ciphertext:
            raise ValueError("Yandex OAuth Client Secret is required before enabling Yandex login")
    if row.metrika_enabled and (not row.metrika_counter_id.isdigit() or int(row.metrika_counter_id) <= 0):
        raise ValueError("Yandex Metrica counter ID must be numeric")
    row.updated_by = actor_id
    row.updated_at = utcnow()
    db.flush()
    return row


def smtp_ready(db: Session, settings) -> bool:
    return bool(integration_snapshot(db, settings).get("smtp", {}).get("ready"))


def send_email(db: Session, settings, *, to_email: str, subject: str, text: str) -> None:
    row = get_owner_settings(db)
    if row is None or not integration_snapshot(db, settings).get("smtp", {}).get("ready"):
        raise RuntimeError("SMTP is not configured")
    password = decrypt_secret(settings, row.smtp_password_ciphertext)
    message = EmailMessage()
    message["Subject"] = subject[:200]
    message["From"] = f"{row.smtp_from_name} <{row.smtp_from_email}>" if row.smtp_from_name else row.smtp_from_email
    message["To"] = to_email
    message.set_content(text)
    timeout = 12
    if row.smtp_use_ssl:
        client = smtplib.SMTP_SSL(row.smtp_host, int(row.smtp_port), timeout=timeout, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(row.smtp_host, int(row.smtp_port), timeout=timeout)
    try:
        client.ehlo()
        if row.smtp_use_tls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if row.smtp_username:
            client.login(row.smtp_username, password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_email_state(db: Session, user: User, *, mark_verified: bool = False) -> UserEmailState:
    state = db.get(UserEmailState, user.id)
    if state is None:
        state = UserEmailState(user_id=user.id, verified_at=utcnow() if mark_verified else None)
        db.add(state)
        db.flush()
    elif mark_verified and state.verified_at is None:
        state.verified_at = utcnow()
        state.updated_at = utcnow()
    return state


def email_verification_required_for_user(db: Session, user: User) -> bool:
    row = get_owner_settings(db)
    if row is None or not row.auth_email_verification_required or user.is_admin:
        return False
    state = db.get(UserEmailState, user.id)
    return state is not None and state.verified_at is None


def create_email_token(db: Session, user: User, *, purpose: str) -> tuple[str, AuthEmailToken]:
    if purpose not in TOKEN_TTL_MINUTES:
        raise ValueError("Unsupported email token purpose")
    now = utcnow()
    db.execute(update(AuthEmailToken).where(AuthEmailToken.user_id == user.id, AuthEmailToken.purpose == purpose, AuthEmailToken.used_at.is_(None)).values(used_at=now))
    raw = secrets.token_urlsafe(32)
    row = AuthEmailToken(user_id=user.id, purpose=purpose, token_hash=_token_hash(raw), expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES[purpose]))
    db.add(row)
    db.flush()
    return raw, row


def consume_email_token(db: Session, *, raw_token: str, purpose: str) -> User:
    now = utcnow()
    row = db.scalar(select(AuthEmailToken).where(AuthEmailToken.token_hash == _token_hash(raw_token.strip()), AuthEmailToken.purpose == purpose, AuthEmailToken.used_at.is_(None)).limit(1))
    if row is None or (_aware(row.expires_at) or now) <= now:
        raise ValueError("Token is invalid or expired")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ValueError("Account is unavailable")
    row.used_at = now
    return user


def send_verification_email(db: Session, settings, user: User) -> None:
    row = get_owner_settings(db)
    if row is None or not row.public_base_url:
        raise RuntimeError("Public base URL is not configured")
    state = ensure_email_state(db, user)
    raw, _ = create_email_token(db, user, purpose="verify_email")
    state.last_verification_sent_at = utcnow()
    url = f"{row.public_base_url.rstrip('/')}/verify-email?token={raw}"
    send_email(db, settings, to_email=user.email, subject="Подтвердите email для X1 AI", text=f"Подтвердите адрес электронной почты, чтобы пользоваться X1 AI:\n\n{url}\n\nСсылка действует 24 часа. Если вы не создавали аккаунт, письмо можно проигнорировать.")


def send_password_reset_email(db: Session, settings, user: User) -> None:
    row = get_owner_settings(db)
    if row is None or not row.public_base_url:
        raise RuntimeError("Public base URL is not configured")
    state = ensure_email_state(db, user, mark_verified=True)
    raw, _ = create_email_token(db, user, purpose="password_reset")
    state.last_password_reset_sent_at = utcnow()
    url = f"{row.public_base_url.rstrip('/')}/reset-password?token={raw}"
    send_email(db, settings, to_email=user.email, subject="Восстановление доступа к X1 AI", text=f"Для смены пароля откройте ссылку:\n\n{url}\n\nСсылка действует 45 минут. Если вы не запрашивали восстановление, ничего делать не нужно.")


def metrika_goal_catalog() -> list[dict[str, str]]:
    return [
        {"id": "landing_register_click", "meaning": "Клик по основному CTA регистрации"},
        {"id": "registration_success", "meaning": "Успешная регистрация"},
        {"id": "login_success", "meaning": "Успешный вход"},
        {"id": "yandex_login_started", "meaning": "Начат вход через Яндекс"},
        {"id": "yandex_login_success", "meaning": "Успешный вход через Яндекс"},
        {"id": "first_prompt_sent", "meaning": "Первый запрос после регистрации"},
        {"id": "answer_success", "meaning": "Получен успешный ответ"},
        {"id": "project_created", "meaning": "Создан проект"},
        {"id": "file_uploaded", "meaning": "Загружен файл"},
        {"id": "pricing_click", "meaning": "Переход к покупке тарифа"},
        {"id": "checkout_started", "meaning": "Создан платежный checkout"},
        {"id": "payment_success", "meaning": "Платеж подтвержден сервером"},
        {"id": "support_ticket_created", "meaning": "Создан тикет поддержки"},
    ]


def public_analytics_config(db: Session) -> dict:
    row = get_owner_settings(db)
    if row is None or not row.metrika_enabled:
        return {"enabled": False, "counter_id": None, "webvisor": False, "goals": metrika_goal_catalog()}
    counter = str(row.metrika_counter_id or "").strip()
    enabled = counter.isdigit() and int(counter) > 0
    return {"enabled": enabled, "counter_id": int(counter) if enabled else None, "webvisor": bool(row.metrika_webvisor), "goals": metrika_goal_catalog()}
