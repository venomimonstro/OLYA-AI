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
    "support_tickets",
    "support_messages",
    "payment_provider_attempts",
}

METRIKA_GOALS = {
    "landing_register_click",
    "registration_success",
    "login_success",
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

    _require(
        errors,
        "app/services/payment_providers.py",
        (
            "hmac.new",
            "hashlib.sha256",
            "hmac.compare_digest",
            'quote_via=quote',
            'if k != "sign"',
            "test_notification",
            "unaccepted",
            "withdraw_amount",
            "currency",
            "verify_yookassa_payment",
            "validate_yookassa_against_checkout",
            'f"{YOOKASSA_API}/payments/{payment_id}"',
            'auth=(owner.yookassa_shop_id, secret)',
        ),
        "payment_provider_verification_missing",
    )
    providers = _text("app/services/payment_providers.py")
    if "sha1_hash" in providers:
        errors.append({"code": "deprecated_yoomoney_sha1_must_not_be_used"})

    _require(
        errors,
        "app/api/routes/payment_providers.py",
        (
            "MAX_WEBHOOK_BODY = 64 * 1024",
            "strict_parsing=True",
            "PaymentProviderAttempt.provider_payment_id == payment_id",
            "verified = await verify_yookassa_payment",
            "apply_payment_record",
            'checkout.status = "canceled"',
        ),
        "payment_webhook_hardening_missing",
    )
    _require(
        errors,
        "app/services/owner_integrations.py",
        (
            "encrypt_secret",
            "decrypt_secret",
            "smtp_auth_ready",
            "auth_email_verification_required",
            "setUserID" if False else "metrika_goal_catalog",
        ),
        "owner_integration_contract_missing",
    )
    integration_service = _text("app/services/owner_integrations.py")
    # Read models may expose only whether a secret exists, never ciphertext or plaintext.
    if '"smtp_password":' in integration_service or '"yookassa_secret":' in integration_service or '"yoomoney_notification_secret":' in integration_service:
        errors.append({"code": "integration_snapshot_may_expose_secret"})

    _require(
        errors,
        "app/api/routes/auth.py",
        (
            "send_verification_email",
            "email_verification_required_for_user",
            "password-reset/request",
            "password-reset/confirm",
            "verification/resend",
            "update(AuthSession)",
        ),
        "auth_recovery_contract_missing",
    )
    _require(
        errors,
        "app/api/routes/support.py",
        (
            "open_count >= 3",
            'row.status = "waiting_admin"',
            'row.status = "waiting_user"',
            "support.reply",
            "support.update",
        ),
        "support_contract_missing",
    )

    _require(
        errors,
        "app/public_ui.py",
        (
            "viewport",
            "Попробовать бесплатно",
            "Тарифы",
            "Частые вопросы",
            "landing_register_click",
            "registration_success",
            "setUserID",
            "mc.yandex.ru",
            "@media(max-width:560px)",
        ),
        "public_conversion_surface_missing",
    )
    public_ui = _text("app/public_ui.py")
    for forbidden in ("email:email", "prompt:", "message_text", "chat_text"):
        if forbidden in public_ui:
            errors.append({"code": "metrika_sensitive_payload_risk", "token": forbidden})

    _require(
        errors,
        "app/user_ui.py",
        (
            "/support",
            "/v1/commerce/billing/providers",
            "/v1/commerce/billing/provider-checkout",
            "first_prompt_sent",
            "answer_success",
            "x1_user_id",
        ),
        "workspace_launch_ux_missing",
    )
    _require(
        errors,
        "app/admin_ui.py",
        ("/admin/owner", "/admin/integrations", "/admin/support", "/v1/admin/owner-dashboard?days=30"),
        "owner_control_center_navigation_missing",
    )
    _require(
        errors,
        "app/owner_dashboard_ui.py",
        ("MRR", "ARPPU", "activation", "D7 retention", "request success", "support ждёт", "SMTP", "ЮKassa", "ЮMoney"),
        "owner_dashboard_metric_missing",
    )

    _require(
        errors,
        "scripts/install_starter_6gb.sh",
        (
            "docker.io",
            "docker-compose-v2",
            "X1_INITIAL_ADMIN_EMAIL",
            "X1_INITIAL_ADMIN_PASSWORD",
            "scripts.create_admin --stdin-json",
            "X1_SERVER_OPTIMIZATION_PROFILE','starter_6gb'",
            "Qwen3-4B-Q4_K_M",
        ),
        "starter_one_command_install_missing",
    )
    _require(
        errors,
        "scripts/bootstrap.sh",
        ("X1_INSTALL_PROFILE", "starter_6gb", "profile_from_host"),
        "profile_aware_bootstrap_missing",
    )
    _require(
        errors,
        "scripts/update.sh",
        ("X1_SKIP_ADMIN_SETUP=1", "profile_now", "starter_6gb", "install_revision"),
        "profile_aware_update_missing",
    )

    _require(
        errors,
        "app/services/api_access.py",
        ("rate_limit", "window", "ApiRateWindow"),
        "api_abuse_guard_missing",
    )
    _require(
        errors,
        "app/services/progressive_launch.py",
        ("public_launch_max_requests_per_user_hour", "UserCircuitBreaker"),
        "chat_abuse_breaker_missing",
    )
    _require(
        errors,
        "app/services/resource_governor.py",
        ("max_queue", "max_queued_per_principal", "principal_rejections"),
        "inference_queue_guard_missing",
    )
    _require(
        errors,
        "app/services/quota.py",
        ("monthly_request_units", "daily_request_units", "compute"),
        "commercial_quota_guard_missing",
    )

    _require(
        errors,
        "app/inference/router.py",
        (
            "starter_4k",
            'max_output_tokens=448 if starter_4k',
            'max_output_tokens=1024 if starter_4k',
            'mode="work"',
        ),
        "starter_answer_quality_envelope_missing",
    )
    _require(
        errors,
        "app/services/operations_analytics.py",
        ("p95_duration_ms", "p95_queue_ms", "cpu_seconds_per_success", "waste_rate", "'applied'"),
        "owner_performance_economics_missing",
    )

    runner = _text("scripts/run_full_regression.py")
    if "scripts.final_launch_hardening_audit" not in runner:
        errors.append({"code": "final_launch_audit_not_release_gated"})

    return {
        "format": "x1-final-launch-hardening-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "static_launch_contract": {
            "auth_email": True,
            "support_tickets": True,
            "owner_dashboard": True,
            "smtp_admin": True,
            "metrika_funnel": True,
            "yoomoney_hmac_sha256": True,
            "yookassa_server_verification": True,
            "one_command_starter": True,
            "abuse_guards": True,
            "starter_answer_quality_envelope": True,
        },
        "live_launch_gate_note": "Static pass is necessary but never sufficient. Target-node load, SMTP delivery, DNS/TLS, provider payments/webhooks, backup/restore and release acceptance must pass live before public launch.",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
