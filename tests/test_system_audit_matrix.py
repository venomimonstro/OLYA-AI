from __future__ import annotations
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from app.models_sprint31 import SystemCheckpoint
from app.services import system_observability as obs

def test_positive_route_contract_is_stable():
    paths=['/v1/auth/register','/v1/chat','/v1/projects','/v1/documents','/v1/images','/v1/research','/v1/development','/v1/engineering','/v1/sandbox','/v1/git','/v1/commerce/plans','/v1/api/chat','/v1/complaints','/v1/admin/beta/participants','/v1/admin/reliability/status','/v1/admin/operations/health']
    app=SimpleNamespace(routes=[SimpleNamespace(path=p) for p in paths])
    assert obs._route_contract(app)['status']==obs.STABLE

def test_negative_missing_feature_is_critical():
    paths=['/v1/auth/register','/v1/chat','/v1/projects','/v1/documents','/v1/images','/v1/research','/v1/development','/v1/engineering','/v1/sandbox','/v1/git','/v1/api/chat','/v1/complaints','/v1/admin/beta/participants','/v1/admin/reliability/status','/v1/admin/operations/health']
    app=SimpleNamespace(routes=[SimpleNamespace(path=p) for p in paths])
    result=obs._route_contract(app)
    assert result['status']==obs.CRITICAL and 'commerce' in result['details']['missing_features']

def test_dependency_root_cause_is_propagated():
    checks=[obs._check('core.inference','inference',obs.CRITICAL,'down',critical=True),obs._check('link.chat','chat',obs.DEGRADED,'bad',dependency='core.inference')]
    roots=obs._root_causes(checks)
    assert roots==['core.inference'] and checks[1]['root_cause']=='core.inference'

def test_backup_checksum_tamper_is_detected(tmp_path:Path):
    folder=tmp_path/'snap'; folder.mkdir(); target=folder/'database.dump'; target.write_bytes(b'good')
    digest=hashlib.sha256(b'good').hexdigest(); (folder/'SHA256SUMS').write_text(f'{digest}  database.dump\n')
    assert obs._verify_manifest(folder)==[]
    target.write_bytes(b'bad'); assert obs._verify_manifest(folder)==['checksum:database.dump']

def test_stale_checkpoint_is_unknown(db_session):
    old=datetime.now(timezone.utc)-timedelta(hours=1)
    row=SystemCheckpoint(key='probe',subsystem='core',status='stable',last_checked_at=old,last_ok_at=old)
    db_session.add(row); db_session.commit()
    item=next(x for x in obs.latest_checkpoints(db_session,stale_after_seconds=60) if x['key']=='probe')
    assert item['status']==obs.UNKNOWN and item['stale'] is True and item['recommended_action']
