from __future__ import annotations

from app.image_studio_ui import image_studio
from app.main import app
from scripts.image_studio_beta_audit import audit


def test_sprint77_beta_contract_route_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/images/beta-contract" in paths
    assert "/studio" in paths


def test_sprint77_studio_is_fail_closed_and_nonce_protected() -> None:
    response = image_studio()
    body = response.body.decode("utf-8")
    csp = response.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert "'unsafe-inline'" not in csp
    assert "localStorage" not in body
    assert "/v1/images/beta-contract" in body
    assert "Capability contract недоступен — запуск заблокирован." in body
    assert "strict_quality:true" in body


def test_sprint77_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
