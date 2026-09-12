from __future__ import annotations

from app.api.routes.capabilities import capabilities_console
from app.main import app
from app.services.capabilities import CAPABILITY_IDS, capability_for_request
from scripts.capability_registry_audit import audit


def test_sprint73_capability_routes_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/capabilities" in paths
    assert "/v1/admin/capabilities" in paths
    assert "/admin/capabilities" in paths


def test_sprint73_registry_has_stable_unique_ids() -> None:
    assert len(CAPABILITY_IDS) == len(set(CAPABILITY_IDS))
    assert {
        "chat",
        "research.fetch",
        "research.search",
        "images.generate",
        "images.edit",
        "sandbox.execute",
        "development",
        "api",
        "billing",
    }.issubset(set(CAPABILITY_IDS))


def test_sprint73_request_mapping_is_deterministic() -> None:
    assert capability_for_request("POST", "/v1/chat") == "chat"
    assert capability_for_request("POST", "/v1/research/sources") == "research.fetch"
    assert capability_for_request("POST", "/v1/research/runs/abc/discover") == "research.search"
    assert capability_for_request("POST", "/v1/images/generations") == "images.generate"
    assert capability_for_request("POST", "/v1/images/edits") == "images.edit"
    assert capability_for_request("POST", "/v1/project-sandboxes/abc/execute") == "sandbox.execute"
    assert capability_for_request("GET", "/v1/chat") is None


def test_sprint73_admin_capability_ui_is_nonce_protected() -> None:
    response = capabilities_console()
    body = response.body.decode("utf-8")
    csp = response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in csp
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert "localStorage" not in body
    assert "onclick=" not in body
    assert "/v1/admin/capabilities?live=" in body


def test_sprint73_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
