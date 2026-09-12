from __future__ import annotations

from app.admin_users_ui import admin_users_console
from app.main import app
from scripts.admin_user_operations_audit import audit


def _route(path: str):
    return next((row for row in app.routes if getattr(row, "path", None) == path), None)


def test_sprint72_admin_user_routes_are_registered() -> None:
    expected = {
        "/admin/users": "GET",
        "/v1/admin/users/search": "GET",
        "/v1/admin/users/{user_id}/operations": "GET",
        "/v1/admin/users/{user_id}/control": "PUT",
        "/v1/admin/users/{user_id}/control/clear": "POST",
        "/v1/admin/users/{user_id}/state": "POST",
        "/v1/admin/users/{user_id}/sessions/revoke": "POST",
    }
    for path, method in expected.items():
        route = _route(path)
        assert route is not None, path
        assert method in (getattr(route, "methods", set()) or set())


def test_sprint72_user_console_is_nonce_protected_and_secret_minimal() -> None:
    response = admin_users_console()
    body = response.body.decode("utf-8")
    csp = response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in csp
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert "localStorage" not in body
    assert "onclick=" not in body
    assert "password_hash" not in body
    assert "token_hash" not in body
    assert "/v1/admin/users/search?q=" in body
    assert "expected_version" in body
    assert "confirm_email" in body


def test_sprint72_admin_user_operations_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
