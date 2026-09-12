from __future__ import annotations
from app.main import app
from scripts.frontend_polish_audit import audit

def test_sprint79_primary_product_surfaces_registered()->None:
    paths={getattr(r,'path','') for r in app.routes}
    for path in ('/app','/studio','/admin','/admin/users','/admin/capabilities','/admin/analytics'):
        assert path in paths

def test_sprint79_frontend_contract_audit_passes()->None:
    result=audit(); assert result['status']=='passed',result['errors']
    assert result['composition_layer_deferred_to_sprint83'] is True
