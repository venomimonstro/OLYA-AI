from __future__ import annotations
from app.main import app
from app.api.routes.product_analytics import analytics_page
from scripts.product_analytics_audit import audit

def test_sprint78_routes_registered()->None:
    paths={getattr(r,'path','') for r in app.routes}
    assert '/v1/admin/product-analytics' in paths
    assert '/admin/analytics' in paths

def test_sprint78_analytics_ui_is_private()->None:
    response=analytics_page(); body=response.body.decode('utf-8'); csp=response.headers['content-security-policy']
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert 'localStorage' not in body
    assert 'prompt/message content' in body

def test_sprint78_contract_audit_passes()->None:
    result=audit(); assert result['status']=='passed',result['errors']
