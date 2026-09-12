from __future__ import annotations

from app.admin_ui import admin_console
from app.main import app
from scripts.admin_control_center_audit import audit


def test_sprint71_control_center_route_registered() -> None:
    route = next((r for r in app.routes if getattr(r, 'path', None) == '/v1/admin/operations/control-center'), None)
    assert route is not None
    assert 'GET' in (getattr(route, 'methods', set()) or set())


def test_sprint71_admin_ui_is_nonce_protected_and_read_only() -> None:
    response = admin_console()
    body = response.body.decode('utf-8')
    csp = response.headers['content-security-policy']
    assert "'unsafe-inline'" not in csp
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert '/v1/admin/operations/control-center?window_hours=24' in body
    assert '/v1/admin/reliability/release-readiness?refresh=false' in body
    assert 'href="/admin/beta"' in body
    assert 'href="/admin/launch"' in body
    assert 'href="/admin/media"' in body
    assert 'localStorage' not in body
    assert 'onclick=' not in body
    assert "method:'PATCH'" not in body
    assert "method:'DELETE'" not in body


def test_sprint71_contract_audit_passes() -> None:
    result = audit()
    assert result['status'] == 'passed', result['errors']
