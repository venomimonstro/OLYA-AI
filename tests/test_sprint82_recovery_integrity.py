from __future__ import annotations
from scripts.recovery_integrity_audit import audit

def test_sprint82_recovery_contract_audit_passes()->None:
    result=audit(); assert result['status']=='passed',result['errors']
