#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    from app.main import app
    from app.schemas.commerce import ApiKeyCreated, ApiKeyRead
    from app.services.commerce import ALLOWED_API_SCOPES

    errors: list[dict] = []
    source = (ROOT / "app" / "api_console.py").read_text("utf-8")
    commerce = (ROOT / "app" / "api" / "routes" / "commerce.py").read_text("utf-8")
    routes = {(method, str(getattr(route, "path", ""))) for route in app.routes for method in set(getattr(route, "methods", set()) or set())}

    required_routes = {
        ("GET", "/v1/commerce/console"),
        ("GET", "/v1/commerce/api-telemetry"),
        ("GET", "/v1/commerce/api-keys"),
        ("POST", "/v1/commerce/api-keys"),
        ("DELETE", "/v1/commerce/api-keys/{api_key_id}"),
        ("POST", "/v1/commerce/api-keys/{api_key_id}/rotate"),
        ("GET", "/v1/api/contexts"),
        ("POST", "/v1/api/contexts"),
        ("DELETE", "/v1/api/contexts/{context_id}"),
        ("POST", "/v1/api/chat"),
    }
    for method, path in sorted(required_routes - routes):
        errors.append({"code": "console_route_missing", "method": method, "path": path})

    expected_scopes = {"chat", "contexts:read", "contexts:write", "telemetry:read"}
    if set(ALLOWED_API_SCOPES) != expected_scopes:
        errors.append({"code": "console_scope_catalog_drift", "actual": sorted(ALLOWED_API_SCOPES)})
    for scope in sorted(expected_scopes):
        if f'value="{scope}"' not in source:
            errors.append({"code": "console_scope_missing", "scope": scope})

    for needle in (
        "/v1/commerce/api-keys",
        "/rotate",
        "/v1/commerce/api-telemetry",
        "/v1/api/contexts",
        "/v1/api/chat",
        "Idempotency-Key",
    ):
        if needle not in source:
            errors.append({"code": "console_contract_missing", "value": needle})

    if "token" in ApiKeyRead.model_fields or "secret_hash" in ApiKeyRead.model_fields:
        errors.append({"code": "console_key_list_exposes_secret"})
    if "token" not in ApiKeyCreated.model_fields:
        errors.append({"code": "console_one_time_secret_missing"})
    if "ApiRequestTelemetry" not in commerce or "api_key_id.in_(selected_ids)" not in commerce:
        errors.append({"code": "console_telemetry_not_owner_scoped"})
    if "row.owner_id==user.id" not in commerce:
        errors.append({"code": "console_key_owner_check_missing"})

    # API secrets may live in a JS variable/input for the current page, but must
    # never be persisted in browser storage. The user session token is exempt.
    forbidden_secret_storage = (
        "localStorage.setItem('x1_api",
        'localStorage.setItem("x1_api',
        "sessionStorage.setItem('x1_api",
        'sessionStorage.setItem("x1_api',
        "sessionStorage.setItem('apiSecret",
        'sessionStorage.setItem("apiSecret',
        "localStorage.setItem('apiSecret",
        'localStorage.setItem("apiSecret',
    )
    for pattern in forbidden_secret_storage:
        if pattern in source:
            errors.append({"code": "console_secret_persisted", "pattern": pattern})

    if "textContent=apiSecret" not in source or "secret-panel" not in source:
        errors.append({"code": "console_one_time_secret_ui_missing"})
    if "autocomplete=\"off\"" not in source or "type=\"password\"" not in source:
        errors.append({"code": "console_secret_input_not_hardened"})
    if "Content-Security-Policy" not in source or "connect-src 'self'" not in source:
        errors.append({"code": "console_csp_missing"})
    if "credentials:'omit'" not in source:
        errors.append({"code": "console_fetch_credentials_not_omitted"})

    return {
        "format": "x1-api-console-audit-v1",
        "status": "passed" if not errors else "failed",
        "required_routes": [{"method": m, "path": p} for m, p in sorted(required_routes)],
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
