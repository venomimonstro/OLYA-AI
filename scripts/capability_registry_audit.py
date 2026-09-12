#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    service = (ROOT / "app/services/capabilities.py").read_text("utf-8")
    routes = (ROOT / "app/api/routes/capabilities.py").read_text("utf-8")
    admin_ui = (ROOT / "app/admin_ui.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required_service = [
        'REGISTRY_VERSION = "x1-capabilities-v1"',
        '"chat"',
        '"research.fetch"',
        '"research.search"',
        '"images.generate"',
        '"images.edit"',
        '"sandbox.execute"',
        '"development"',
        '"api"',
        '"billing"',
        '"available": failed is None',
        '"reason": reason',
        '"requirements": requirements',
        '"account_active"',
        '"account_inactive"',
        '"monthly_compute_budget"',
        '"compute_quota_exhausted"',
        "compute_seconds_used",
        "active_restriction",
        "image_worker_snapshot",
        "image_edit_capabilities",
        "sandbox_capabilities",
        "get_or_create_quota",
    ]
    for token in required_service:
        if token not in service:
            errors.append({"code": "capability_registry_contract_missing", "token": token})

    if service.find('"account_active"') > service.find("_restriction_requirement(db, user, capability_id)"):
        errors.append({"code": "account_state_must_precede_capability_policy"})
    if service.count("_compute_budget(db, user, quota)") < 2:
        errors.append({"code": "compute_budget_not_applied_to_chat_and_development"})
    if 'reason in {"account_inactive", "safety_restriction_active"}' not in service:
        errors.append({"code": "capability_http_status_missing_account_guard"})
    if 'reason in {"active_image_limit", "compute_quota_exhausted"}' not in service:
        errors.append({"code": "capability_http_status_missing_quota_guard"})

    required_routes = [
        '@router.get("/v1/capabilities")',
        '@router.get("/v1/admin/capabilities")',
        '@router.get("/admin/capabilities"',
        "Depends(get_current_user)",
        "Depends(require_admin)",
        "registry_payload",
        "capability_decision",
        "live: bool = Query(default=False)",
    ]
    for token in required_routes:
        if token not in routes:
            errors.append({"code": "capability_route_contract_missing", "token": token})

    required_registration = [
        "from app.api.routes.capabilities import router as capabilities_router",
        "router.include_router(capabilities_router)",
        'href="/admin/capabilities"',
    ]
    for token in required_registration:
        if token not in admin_ui:
            errors.append({"code": "capability_registration_missing", "token": token})

    for source_name, source in (("routes", routes), ("admin_ui", admin_ui)):
        for forbidden in ("localStorage", "'unsafe-inline'", "onclick="):
            if forbidden in source:
                errors.append({"code": "capability_ui_unsafe_pattern", "source": source_name, "token": forbidden})

    if "sessionStorage.setItem('x1AdminToken'" not in routes:
        errors.append({"code": "capability_admin_token_storage_contract_missing"})
    if "credentials:'omit'" not in routes:
        errors.append({"code": "capability_admin_fetch_credentials_contract_missing"})
    if "nonce = secrets.token_urlsafe" not in routes or "script-src 'nonce-" not in routes or "style-src 'nonce-" not in routes:
        errors.append({"code": "capability_ui_nonce_csp_missing"})

    forbidden_payload_tokens = [
        '"project_sandbox_worker_token"',
        '"document_render_worker_token"',
        '"brave_search_api_key"',
        '"image_model_path"',
        '"image_edit_model_path"',
        '"payment_ingest_secret"',
    ]
    for token in forbidden_payload_tokens:
        if token in routes:
            errors.append({"code": "capability_secret_surface_leak", "token": token})

    if '("scripts.capability_registry_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_capability_registry_audit"})

    return {
        "format": "x1-capability-registry-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
