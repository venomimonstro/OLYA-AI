from app.main import app
from app.services.business_contract import CONTRACT_VERSION
from scripts.business_logic_contract_audit import audit


def test_business_logic_contract_route_is_registered():
    routes = {(method, route.path) for route in app.routes for method in (getattr(route, "methods", None) or set())}
    assert ("GET", "/v1/admin/reliability/business-contract") in routes


def test_business_contract_version_is_frozen():
    assert CONTRACT_VERSION == "x1-business-contract-v1"


def test_business_logic_contract_audit_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]
