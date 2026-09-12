from scripts.architecture_cleanup_audit import audit
from app.domain_ownership import DOMAIN_OWNERS, domain_owner


def test_architecture_cleanup_contract_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]


def test_cross_cutting_domains_have_one_owner():
    assert len(DOMAIN_OWNERS) == len(set(DOMAIN_OWNERS))
    assert domain_owner("quota_entitlement") == "app.services.quota"
    assert domain_owner("admin_user_override") == "app.services.admin_user_controls"
    assert domain_owner("capability_availability") == "app.services.capabilities"
    assert domain_owner("recovery_integrity") == "app.services.recovery_integrity"
