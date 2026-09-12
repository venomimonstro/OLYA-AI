from __future__ import annotations

from pathlib import Path

from scripts.account_plan_ux_audit import audit as audit_account_plan_ux


def test_workspace_exposes_real_account_and_plan_controls(client):
    response = client.get("/app")
    assert response.status_code == 200
    html = response.text
    for element_id in (
        "account-subscription",
        "account-commerce-usage",
        "account-resource-progress",
        "account-plans",
        "account-checkouts",
        "account-payments",
        "subscription-cancel",
        "subscription-resume",
        "api-console-open",
    ):
        assert f'id="{element_id}"' in html
    assert "/v1/commerce/billing/checkout" in html
    assert "/v1/commerce/billing/subscription/cancel" in html
    assert "/v1/commerce/billing/subscription/resume" in html
    assert "/v1/commerce/console" in html
    assert "X-X1-Payment-Secret" not in html
    assert "payment_ingest_secret" not in html


def test_account_plan_uses_server_catalog_and_free_state(register_user, client):
    _, headers = register_user("sprint70-account@example.com")
    plans = client.get("/v1/commerce/billing/plans", headers=headers)
    usage = client.get("/v1/commerce/usage", headers=headers)
    subscription = client.get("/v1/commerce/billing/subscription", headers=headers)
    assert plans.status_code == 200
    assert usage.status_code == 200
    assert subscription.status_code == 200
    catalog = plans.json()
    assert any(row["name"] == "free" for row in catalog)
    assert any(row["name"] == "x1" and row["amount_minor"] > 0 for row in catalog)
    assert usage.json()["plan"] == "free"
    assert subscription.json() is None


def test_account_checkout_contract_contains_no_client_price_fields(client):
    html = client.get("/app").text
    assert "JSON.stringify({plan:plan,idempotency_key:checkoutKey(plan)})" in html
    assert "JSON.stringify({plan:plan,amount_minor" not in html
    assert "JSON.stringify({plan:plan,currency" not in html
    assert "Доступ изменится только после подтверждения оплаты." in html


def test_account_plan_ux_is_release_gated():
    report = audit_account_plan_ux()
    assert report["status"] == "passed", report["errors"]
    runner = (Path(__file__).resolve().parents[1] / "scripts" / "run_full_regression.py").read_text("utf-8")
    assert "scripts.account_plan_ux_audit" in runner
