from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.main import app as x1_app
from app.models import BackgroundJob, ComplaintCase, RegressionCase, SystemCheckpoint, User
from app.services.reliability import collect_system_health


def reg(client, email):
    r = client.post('/v1/auth/register', json={'email': email, 'password': 'Secret123!', 'display_name': 'x'})
    assert r.status_code == 201
    return r.json()['access_token'], r.json()['user_id']


def _patch_storage(monkeypatch, tmp_path):
    s = x1_app.state.settings
    monkeypatch.setattr(s, 'file_storage_path', str(tmp_path / 'files'))
    monkeypatch.setattr(s, 'document_storage_path', str(tmp_path / 'docs'))
    monkeypatch.setattr(s, 'code_workspace_storage_path', str(tmp_path / 'code'))
    monkeypatch.setattr(s, 'project_runtime_storage_path', str(tmp_path / 'runtime'))


def test_positive_system_health_is_stable(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(x1_app.state.settings, 'admin_bootstrap_token', 'safe-token')
    monkeypatch.setattr(x1_app.state.settings, 'project_runtime_secret_key', 'safe-runtime-secret')
    async def ok(): return True
    monkeypatch.setattr(x1_app.state.llama, 'health', ok)
    import asyncio
    result = asyncio.run(collect_system_health(x1_app, db_session, persist=True, deep=True))
    assert result['status'] == 'stable'
    assert result['critical_failed'] == 0
    assert db_session.query(SystemCheckpoint).count() >= 10


def test_negative_inference_failure_is_immediately_critical(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(x1_app.state.settings, 'admin_bootstrap_token', 'safe-token')
    monkeypatch.setattr(x1_app.state.settings, 'project_runtime_secret_key', 'safe-runtime-secret')
    async def down(): return False
    monkeypatch.setattr(x1_app.state.llama, 'health', down)
    import asyncio
    result = asyncio.run(collect_system_health(x1_app, db_session, persist=True))
    assert result['status'] == 'failed'
    checkpoint = db_session.query(SystemCheckpoint).filter_by(key='core.inference').one()
    assert checkpoint.status == 'failed'
    assert checkpoint.consecutive_failures == 1


def test_stale_job_lease_marks_runtime_failed(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    async def ok(): return True
    monkeypatch.setattr(x1_app.state.llama, 'health', ok)
    db_session.add(BackgroundJob(kind='probe', status='running', lease_expires_at=datetime.now(timezone.utc)-timedelta(minutes=5)))
    db_session.commit()
    import asyncio
    result = asyncio.run(collect_system_health(x1_app, db_session, persist=False))
    queue = next(x for x in result['checks'] if x['key'] == 'runtime.queue')
    assert queue['status'] == 'failed'


def test_release_blocker_is_visible_as_degradation(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    async def ok(): return True
    monkeypatch.setattr(x1_app.state.llama, 'health', ok)
    complaint = ComplaintCase(component='chat', category='context_loss', severity='critical', title='repeat',
                              actual_behavior='repeat failure', expected_behavior='no failure', reproduction={}, evidence={},
                              fingerprint='a'*64, status='confirmed')
    db_session.add(complaint); db_session.flush()
    db_session.add(RegressionCase(source_complaint_id=complaint.id, fingerprint='a'*64, component='chat', category='context_loss',
                                  severity='critical', title='repeat', spec={}, status='active', confirmed_occurrences=2,
                                  release_blocking=True))
    db_session.commit()
    import asyncio
    result = asyncio.run(collect_system_health(x1_app, db_session, persist=False))
    gate = next(x for x in result['checks'] if x['key'] == 'quality.release_gate')
    assert gate['status'] == 'degraded'


def test_production_default_secret_is_failed(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(x1_app.state.settings, 'env', 'production')
    monkeypatch.setattr(x1_app.state.settings, 'admin_bootstrap_token', 'change-me')
    monkeypatch.setattr(x1_app.state.settings, 'project_runtime_secret_key', 'change-me-runtime-secret')
    async def ok(): return True
    monkeypatch.setattr(x1_app.state.llama, 'health', ok)
    import asyncio
    result = asyncio.run(collect_system_health(x1_app, db_session, persist=False))
    config = next(x for x in result['checks'] if x['key'] == 'core.configuration')
    assert config['status'] == 'failed'


def test_admin_reliability_block_and_access(client, db_session, monkeypatch, tmp_path):
    _patch_storage(monkeypatch, tmp_path)
    async def ok(): return True
    monkeypatch.setattr(x1_app.state.llama, 'health', ok)
    token, uid = reg(client, 'reliability-admin@example.com')
    db_session.get(User, uid).is_admin = True
    db_session.commit()
    h = {'Authorization': f'Bearer {token}'}
    r = client.get('/v1/admin/reliability/status?refresh=true', headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body['status'] in {'stable', 'degraded'}
    assert body['checkpoints']
    assert 'core.database' in {x['key'] for x in body['checkpoints']}
    assert 'Контрольные точки системы' in client.get('/admin').text


def test_non_admin_cannot_read_reliability(client):
    token, _ = reg(client, 'reliability-user@example.com')
    r = client.get('/v1/admin/reliability/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 403
