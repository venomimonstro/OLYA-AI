#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ENDPOINTS = (
    "/v1/commerce/usage",
    "/v1/commerce/billing/plans",
    "/v1/commerce/billing/subscription",
    "/v1/commerce/billing/checkout",
    "/v1/commerce/billing/checkouts",
    "/v1/commerce/billing/payments",
    "/v1/commerce/billing/subscription/cancel",
    "/v1/commerce/billing/subscription/resume",
    "/v1/commerce/console",
)


def audit() -> dict:
    from app.main import app
    from app.user_ui import workspace

    errors: list[dict] = []
    registered = {str(getattr(route, "path", "")) for route in app.routes}
    if "/app" not in registered:
        errors.append({"code": "workspace_route_missing"})

    response = workspace()
    html = response.body.decode("utf-8")
    for endpoint in REQUIRED_ENDPOINTS:
        if endpoint not in html:
            errors.append({"code": "account_plan_endpoint_not_wired", "endpoint": endpoint})

    required_ids = (
        "account-subscription",
        "account-commerce-usage",
        "account-resource-progress",
        "account-plans",
        "account-checkouts",
        "account-payments",
        "subscription-cancel",
        "subscription-resume",
        "api-console-open",
        "billing-state",
    )
    for element_id in required_ids:
        if f'id="{element_id}"' not in html:
            errors.append({"code": "account_plan_element_missing", "id": element_id})

    forbidden = (
        "X-X1-Payment-Secret",
        "payment_ingest_secret",
        "billing_price_x1_minor",
        "amount_minor:plan",
        "currency:plan",
    )
    for needle in forbidden:
        if needle in html:
            errors.append({"code": "account_ui_exposes_payment_internal", "needle": needle})

    if "JSON.stringify({plan:plan,idempotency_key:checkoutKey(plan)})" not in html:
        errors.append({"code": "checkout_payload_not_server_priced"})
    if "credentials:'omit'" not in html:
        errors.append({"code": "workspace_fetch_credentials_contract_missing"})
    if "textContent" not in html or ".innerHTML" in html:
        errors.append({"code": "safe_dom_contract_failed"})
    if "checkout.checkout_url" not in html or "location.assign(target.href)" not in html:
        errors.append({"code": "checkout_redirect_contract_missing"})
    if "Автопродление отключено. Оплаченный период остаётся активным." not in html:
        errors.append({"code": "cancel_at_period_end_copy_missing"})
    if "Доступ изменится только после подтверждения оплаты." not in html:
        errors.append({"code": "payment_confirmation_copy_missing"})

    runner = (ROOT / "scripts" / "run_full_regression.py").read_text("utf-8")
    if "scripts.account_plan_ux_audit" not in runner:
        errors.append({"code": "account_plan_audit_not_release_gated"})

    return {
        "format": "x1-account-plan-ux-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
