#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    models = (ROOT / "app/models.py").read_text("utf-8")
    model = (ROOT / "app/models_sprint72.py").read_text("utf-8")
    migration = (ROOT / "alembic/versions/f72a1c8e5b20_add_admin_user_controls.py").read_text("utf-8")
    generator = (ROOT / "scripts/generate_orm_models.py").read_text("utf-8")
    service = (ROOT / "app/services/admin_user_controls.py").read_text("utf-8")
    quota = (ROOT / "app/services/quota.py").read_text("utf-8")
    routes = (ROOT / "app/api/routes/admin_user_ops.py").read_text("utf-8")
    users_ui = (ROOT / "app/admin_users_ui.py").read_text("utf-8")
    admin_ui = (ROOT / "app/admin_ui.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required = {
        "model_registry": (models, "from app.models_sprint72 import *"),
        "model_table": (model, '__tablename__ = "admin_user_controls"'),
        "model_version": (model, '__mapper_args__ = {"version_id_col": version}'),
        "migration_revision": (migration, 'down_revision = "f69b2c4d8e10"'),
        "migration_table": (migration, '"admin_user_controls"'),
        "explicit_orm_owner": (generator, '"admin_user_controls"'),
        "service_version_conflict": (service, "Admin user control changed concurrently"),
        "service_plan_validation": (service, "runtime_plan_catalog"),
        "service_expiry_bound": (service, "timedelta(days=366)"),
        "quota_canonical": (quota, "_sync_canonical_entitlement"),
        "quota_override": (quota, "apply_control_to_quota"),
        "route_search": (routes, '@router.get("/search")'),
        "route_operations": (routes, '@router.get("/{user_id}/operations")'),
        "route_control": (routes, '@router.put("/{user_id}/control")'),
        "route_state": (routes, '@router.post("/{user_id}/state")'),
        "route_revoke": (routes, '@router.post("/{user_id}/sessions/revoke"'),
        "typed_confirmation": (routes, "Typed confirmation does not match target email"),
        "self_suspend_guard": (routes, "Administrator cannot suspend own account"),
        "self_revoke_guard": (routes, "Use normal logout for the current administrator session"),
        "safety_restrictions": (routes, '"safety_restrictions"'),
        "users_page": (users_ui, "@router.get('/admin/users'"),
        "ui_search": (users_ui, "/v1/admin/users/search?q="),
        "ui_control": (users_ui, "/control',{method:'PUT'"),
        "ui_state": (users_ui, "/state',{method:'POST'"),
        "ui_revoke": (users_ui, "/sessions/revoke',{method:'POST'"),
        "ui_clear": (users_ui, "/control/clear',{method:'POST'"),
        "ui_restrictions": (users_ui, "d.safety_restrictions||[]"),
        "parent_api_registration": (admin_ui, "router.include_router(admin_user_ops_router)"),
        "parent_ui_registration": (admin_ui, "router.include_router(admin_users_ui_router)"),
        "control_center_link": (admin_ui, 'href="/admin/users"'),
    }
    for code, (source, token) in required.items():
        if token not in source:
            errors.append({"code": code, "token": token})

    if quota.find("_sync_canonical_entitlement") > quota.find("apply_control_to_quota"):
        errors.append({"code": "override_precedes_canonical_entitlement"})

    for path, source in (("routes", routes), ("users_ui", users_ui)):
        for forbidden in ("password_hash", "token_hash"):
            if forbidden in source:
                errors.append({"code": "secret_surface_leak", "path": path, "token": forbidden})

    for forbidden in ("localStorage", "onclick=", "'unsafe-inline'"):
        if forbidden in users_ui:
            errors.append({"code": "unsafe_user_ops_ui_pattern", "token": forbidden})
    if "nonce = secrets.token_urlsafe" not in users_ui or "script-src 'nonce-" not in users_ui or "style-src 'nonce-" not in users_ui:
        errors.append({"code": "user_ops_ui_nonce_csp_missing"})
    if "sessionStorage.setItem('x1AdminToken'" not in users_ui or "credentials:'omit'" not in users_ui:
        errors.append({"code": "user_ops_ui_auth_contract_missing"})

    if '("scripts.admin_user_operations_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_admin_user_operations_audit"})

    return {
        "format": "x1-admin-user-operations-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
