from __future__ import annotations
from scripts.security_mvp_simulation import audit

def test_sprint81_security_matrix_passes()->None:
    result=audit(); assert result['status']=='passed',result['errors']
    assert all(result['scenarios'].values())
