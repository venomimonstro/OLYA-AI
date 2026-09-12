from __future__ import annotations
import asyncio,pytest
from scripts.load_acceptance import run
from scripts.load_acceptance_audit import audit

def test_sprint80_rejects_fewer_than_ten_users()->None:
    with pytest.raises(RuntimeError): asyncio.run(run('http://127.0.0.1:1',['x']*9,rounds=1,timeout=5,p95_limit_ms=1000,max_error_rate=.1))

def test_sprint80_contract_audit_passes()->None:
    result=audit(); assert result['status']=='passed',result['errors']; assert result['requires_external_target_run'] is True
