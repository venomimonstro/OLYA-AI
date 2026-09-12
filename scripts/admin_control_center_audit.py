#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    operations = (ROOT / 'app/api/routes/operations_analytics.py').read_text('utf-8')
    ui = (ROOT / 'app/admin_ui.py').read_text('utf-8')
    regression = (ROOT / 'scripts/run_full_regression.py').read_text('utf-8')

    required_endpoint_tokens = [
        "@router.get('/control-center')",
        'Depends(require_admin)',
        'BillingSubscription',
        'BillingCheckout',
        'ApiRequestTelemetry',
        'ComplaintCase',
        'RegressionCase',
        "'overload': overload",
    ]
    for token in required_endpoint_tokens:
        if token not in operations:
            errors.append({'code': 'control_center_contract_missing', 'token': token})

    required_ui_tokens = [
        '/v1/admin/operations/control-center?window_hours=24',
        '/v1/admin/operations/health?refresh=true&deep=',
        '/v1/admin/reliability/release-readiness?refresh=false',
        '/v1/admin/performance/snapshots?limit=8',
        '/v1/admin/audit-log?limit=20',
        'href="/admin/beta"',
        'href="/admin/launch"',
        'href="/admin/media"',
        "sessionStorage.setItem('x1AdminToken'",
    ]
    for token in required_ui_tokens:
        if token not in ui:
            errors.append({'code': 'admin_ui_contract_missing', 'token': token})

    forbidden_ui_tokens = [
        "'unsafe-inline'",
        'localStorage',
        ' onclick=',
        ' style=',
        '/v1/admin/users?limit=',
        "method:'PATCH'",
        "method:'DELETE'",
    ]
    for token in forbidden_ui_tokens:
        if token in ui:
            errors.append({'code': 'admin_ui_forbidden_pattern', 'token': token})

    if 'nonce = secrets.token_urlsafe' not in ui or "script-src 'nonce-" not in ui or "style-src 'nonce-" not in ui:
        errors.append({'code': 'admin_ui_csp_nonce_missing'})

    if '("scripts.admin_control_center_audit", [])' not in regression:
        errors.append({'code': 'full_regression_missing_admin_control_center_audit'})

    return {
        'format': 'x1-admin-control-center-audit-v1',
        'status': 'passed' if not errors else 'failed',
        'errors': errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
