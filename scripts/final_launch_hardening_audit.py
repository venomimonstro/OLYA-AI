#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ROUTES = {
    ("POST", "/v1/auth/verification/resend"),
    ("POST", "/v1/auth/verify-email"),
    ("POST", "/v1/auth/password-reset/request"),
    ("POST", "/v1/auth/password-reset/confirm"),
    ("GET", "/v1/auth/providers"),
    ("GET", "/v1/auth/yandex/start"),
    ("GET", "/v1/auth/yandex/callback"),
    ("POST", "/v1/support/tickets"),
    ("GET", "/v1/support/tickets"),
    ("GET", "/v1/admin/support/tickets"),
    ("GET", "/v1/admin/integrations"),
    ("PUT", "/v1/admin/integrations"),
    ("GET", "/v1/admin/owner-dashboard"),
    ("GET", "/v1/commerce/billing/providers"),
    ("POST", "/v1/commerce/billing/provider-checkout"),
    ("POST", "/v1/commerce/payments/yoomoney/notify"),
    ("POST", "/v1/commerce/payments/yookassa/webhook"),
    ("GET", "/v1/public/analytics-config"),
    ("GET", "/admin/owner"),
    ("GET", "/admin/integrations"),
    ("GET", "/admin/support"),
}

REQUIRED_TABLES = {
    "owner_integration_settings",
    "user_email_states",
    "auth_email_tokens",
    "external_auth_identities",
    "oauth_login_states",
    "support_tickets",
    "support_messages",
    "payment_provider_attempts",
}

METRIKA_GOALS = {
    "landing_register_click",
    "registration_success",
    "login_success",
    "yandex_login_started",
    "yandex_login_success",
    "first_prompt_sent",
    "answer_success",
    "project_created",
    "file_uploaded",
    "pricing_click",
    "checkout_started",
    "payment_success",
    "support_ticket_created",
}


def _text(path: str) -> str:
    return (ROOT / path).read_text("utf-8")


def _require(errors: list[dict], source: str, tokens: tuple[str, ...], code: str) -> None:
    text = _text(source)
    for token in tokens:
        if token not in text:
            errors.append({"code": code, "source": source, "token": token})


def audit() -> dict:
    from app.db import Base
    import app.models  # noqa: F401
    from app.main import app
    from app.services.owner_integrations import metrika_goal_catalog

    errors: list[dict] = []
    routes = {
        (method, str(getattr(route, "path", "")))
        for route in app.routes
        for method in set(getattr(route, "methods", set()) or set())
    }
    for method, path in sorted(REQUIRED_ROUTES - routes):
        errors.append({"code": "launch_route_missing", "method": method, "path": path})

    tables = set(Base.metadata.tables)
    for table in sorted(REQUIRED_TABLES - tables):
        errors.append({"code": "launch_table_missing", "table": table})

    goal_ids = {str(row.get("id")) for row in metrika_goal_catalog()}
    if goal_ids != METRIKA_GOALS:
        errors.append({"code": "metrika_goal_catalog_mismatch", "missing": sorted(METRIKA_GOALS - goal_ids), "extra": sorted(goal_ids - METRIKA_GOALS)})

    _require(errors, "app/main.py", ('"app.api.routes.launch_bundle"', "max_queued_per_principal=settings.inference_max_queued_per_principal"), "launch_bundle_not_runtime_registered")
    _require(errors, "alembic/versions/f88b2e7a6c31_add_yandex_oauth.py", ('down_revision = "f87a1d9c4e20"', '"external_auth_identities"', '"oauth_login_states"', '"yandex_oauth_client_secret_ciphertext"'), "yandex_oauth_migration_contract_missing")

    _require(
        errors,
        "app/services/yandex_oauth.py",
        (
            'YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"',
            'YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"',
            'YANDEX_USERINFO_URL = "https://login.yandex.ru/info"',
            '"code_challenge_method": "S256"',
            '"code_verifier": verifier',
            "secrets.compare_digest(state, cookie_state)",
            "state_hash=_digest(state)",
            ".with_for_update()",
            'headers={"Authorization": f"OAuth {access_token}"',
            "profile.get(\"client_id\")",
            "ExternalAuthIdentity",
            "ensure_email_state(db, user, mark_verified=True)",
        ),
        "yandex_oauth_security_contract_missing",
    )
    yandex_service = _text("app/services/yandex_oauth.py")
    if '"redirect_uri": yandex["redirect_uri"]' in yandex_service[yandex_service.find("async def exchange_code"):]:
        errors.append({"code": "yandex_token_exchange_has_undocumented_redirect_uri"})
    if "access_token=" in _text("app/models_launch.py") or "access_token: Mapped" in _text("app/models_launch.py"):
        errors.append({"code": "yandex_access_token_must_not_be_persisted"})

    _require(
        errors,
        "app/api/routes/yandex_oauth.py",
        (
            "httponly=True",
            'samesite="lax"',
            "secure=_is_prod(request)",
            "db.commit()",
            "consume_state",
            "exchange_code",
            "fetch_profile",
            "create_session",
            "sessionStorage.setItem('x1_access_token'",
            "location.replace('/login?oauth=yandex')",
            '"Cache-Control": "no-store"',
        ),
        "yandex_oauth_route_hardening_missing",
    )

    _require(
        errors,
        "app/services/payment_providers.py",
        (
            "hmac.new", "hashlib.sha256", "hmac.compare_digest", 'quote_via=quote', 'if k != "sign"',
            "test_notification", "unaccepted", "withdraw_amount", "currency", "verify_yookassa_payment",
            "validate_yookassa_against_checkout", 'f"{YOOKASSA_API}/payments/{payment_id}"', 'auth=(owner.yookassa_shop_id, secret)',
        ),
        "payment_provider_verification_missing",
    )
    if "sha1_hash" in _text("app/services/payment_providers.py"):
        errors.append({"code": "deprecated_yoomoney_sha1_must_not_be_used"})

    _require(errors, "app/api/routes/payment_providers.py", ("MAX_WEBHOOK_BODY = 64 * 1024", "strict_parsing=True", "PaymentProviderAttempt.provider_payment_id == payment_id", "verified = await verify_yookassa_payment", "apply_payment_record", 'checkout.status = "canceled"'), "payment_webhook_hardening_missing")
    _require(errors, "app/services/owner_integrations.py", ("encrypt_secret", "decrypt_secret", "smtp_auth_ready", "auth_email_verification_required", "yandex_oauth_enabled", "yandex_oauth_client_secret_ciphertext", "metrika_goal_catalog"), "owner_integration_contract_missing")
    integration_service = _text("app/services/owner_integrations.py")
    for exposed in ('"smtp_password":', '"yookassa_secret":', '"yoomoney_notification_secret":', '"yandex_oauth_client_secret":'):
        if exposed in integration_service:
            errors.append({"code": "integration_snapshot_may_expose_secret", "token": exposed})

    _require(errors, "app/api/routes/auth.py", ("send_verification_email", "email_verification_required_for_user", "password-reset/request", "password-reset/confirm", "verification/resend", "update(AuthSession)"), "auth_recovery_contract_missing")
    _require(errors, "app/api/routes/support.py", ("open_count >= 3", 'row.status = "waiting_admin"', 'row.status = "waiting_user"', "support.reply", "support.update"), "support_contract_missing")

    _require(
        errors,
        "app/public_ui.py",
        (
            "viewport", "Попробовать бесплатно", "Тарифы", "Частые вопросы", "landing_register_click",
            "registration_success", "setUserID", "mc.yandex.ru", "@media(max-width:560px)",
            "/v1/auth/providers", "/v1/auth/yandex/start", "yandex_login_started", "yandex_login_success",
        ),
        "public_conversion_surface_missing",
    )
    public_ui = _text("app/public_ui.py")
    for forbidden in ("email:email", "prompt:", "message_text", "chat_text"):
        if forbidden in public_ui:
            errors.append({"code": "metrika_sensitive_payload_risk", "token": forbidden})

    _require(errors, "app/api/routes/owner_integrations.py", ("yandex_oauth_enabled", "yandex_oauth_client_id", "yandex_oauth_client_secret", "OAuth Client ID", "OAuth Client Secret", "ya-redirect"), "yandex_owner_controls_missing")
    _require(errors, "app/user_ui.py", ("/support", "/v1/commerce/billing/providers", "/v1/commerce/billing/provider-checkout", "first_prompt_sent", "answer_success", "x1_user_id"), "workspace_launch_ux_missing")
    _require(errors, "app/admin_ui.py", ("/admin/owner", "/admin/integrations", "/admin/support", "/v1/admin/owner-dashboard?days=30"), "owner_control_center_navigation_missing")
    _require(errors, "app/owner_dashboard_ui.py", ("MRR", "ARPPU", "activation", "D7 retention", "request success", "support ждёт", "SMTP", "ЮKassa", "ЮMoney"), "owner_dashboard_metric_missing")

    _require(errors, "scripts/install_starter_6gb.sh", ("docker.io", "docker-compose-v2", "X1_INITIAL_ADMIN_EMAIL", "X1_INITIAL_ADMIN_PASSWORD", "scripts.create_admin --stdin-json", "X1_SERVER_OPTIMIZATION_PROFILE','starter_6gb'", "Qwen3-4B-Q4_K_M"), "starter_one_command_install_missing")
    _require(errors, "scripts/bootstrap.sh", ("X1_INSTALL_PROFILE", "starter_6gb", "profile_from_host"), "profile_aware_bootstrap_missing")
    _require(errors, "scripts/update.sh", ("X1_SKIP_ADMIN_SETUP=1", "profile_now", "starter_6gb", "install_revision"), "profile_aware_update_missing")

    _require(errors, "app/services/api_access.py", ("ApiRateLimitWindow", "request_count < api_key.rate_limit_per_minute", "Retry-After", "X-RateLimit-Remaining"), "api_abuse_guard_missing")
    _require(errors, "app/services/progressive_launch.py", ("public_launch_max_requests_per_user_hour", "trip_breaker", 'scope=f"user:{user_id}"'), "chat_abuse_breaker_missing")
    _require(errors, "app/services/resource_governor.py", ("max_queue", "max_queued_per_principal", "principal_rejections"), "inference_queue_guard_missing")
    _require(errors, "app/services/quota.py", ("monthly_request_units", "daily_request_units", "compute"), "commercial_quota_guard_missing")

    _require(errors, "app/inference/router.py", ("starter_4k", 'max_output_tokens=448 if starter_4k', 'max_output_tokens=1024 if starter_4k', 'mode="work"'), "starter_answer_quality_envelope_missing")
    _require(errors, "app/services/operations_analytics.py", ("p95_duration_ms", "p95_queue_ms", "cpu_seconds_per_success", "waste_rate", "'applied'"), "owner_performance_economics_missing")

    if "scripts.final_launch_hardening_audit" not in _text("scripts/run_full_regression.py"):
        errors.append({"code": "final_launch_audit_not_release_gated"})

    return {
        "format": "x1-final-launch-hardening-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "static_launch_contract": {
            "auth_email": True,
            "yandex_oauth": True,
            "support_tickets": True,
            "owner_dashboard": True,
            "smtp_admin": True,
            "metrika_funnel": True,
            "yoomoney_hmac_sha256": True,
            "yookassa_server_verification": True,
            "one_command_starter": True,
            "abuse_guards": True,
            "starter_answer_quality_envelope": True,
            "launch_bundle_registered": True,
        },
        "live_launch_gate_note": "Static pass is necessary but never sufficient. Target-node load, Yandex OAuth callback, SMTP delivery, DNS/TLS, provider payments/webhooks, backup/restore and release acceptance must pass live before public launch.",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
