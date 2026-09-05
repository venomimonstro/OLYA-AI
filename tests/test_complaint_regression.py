from sqlalchemy import select

from app.models_sprint30 import RegressionCase


def _reg(client, email):
    r = client.post('/v1/auth/register', json={'email': email, 'password': 'Secret123!', 'display_name': 'x'})
    assert r.status_code == 201, r.text
    return r.json()['access_token'], r.json()['user_id']


def _admin(client, db_session, email='s30-admin@example.com'):
    from app.models import User
    token, uid = _reg(client, email)
    db_session.get(User, uid).is_admin = True
    db_session.commit()
    return {'Authorization': f'Bearer {token}'}, uid


def _payload(actual='Assistant says done although required test still fails'):
    return {
        'component': 'engineering', 'category': 'false_completion', 'severity': 'high',
        'title': 'False completion claim', 'expected_behavior': 'Do not complete until tests pass',
        'actual_behavior': actual,
        'reproduction': {'steps': ['create failing test', 'ask agent to finish']},
        'evidence': {'test': 'failed'},
    }


def test_confirmed_complaint_creates_regression_case(client, db_session):
    user_token, _ = _reg(client, 's30-user1@example.com')
    admin_h, _ = _admin(client, db_session)
    created = client.post('/v1/feedback/complaints', headers={'Authorization':f'Bearer {user_token}'}, json=_payload())
    assert created.status_code == 201, created.text
    confirmed = client.post(f"/v1/admin/complaints/{created.json()['id']}/confirm", headers=admin_h,
                            json={'reproduction':{'verified':True}})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body['complaint']['status'] == 'confirmed'
    assert body['regression_case']['confirmed_occurrences'] == 1
    assert body['regression_case']['release_blocking'] is False


def test_repeat_confirmed_defect_blocks_stable_until_pass(client, db_session):
    user_token, _ = _reg(client, 's30-user2@example.com')
    admin_h, _ = _admin(client, db_session, 's30-admin2@example.com')
    for _ in range(2):
        r = client.post('/v1/feedback/complaints', headers={'Authorization':f'Bearer {user_token}'}, json=_payload())
        c = client.post(f"/v1/admin/complaints/{r.json()['id']}/confirm", headers=admin_h, json={})
        assert c.status_code == 200, c.text
    regression = db_session.scalar(select(RegressionCase))
    assert regression.confirmed_occurrences == 2
    assert regression.release_blocking is True
    gate = client.post('/v1/admin/release-gate/0.30.0', headers=admin_h)
    assert gate.status_code == 200
    assert gate.json()['decision'] == 'blocked'
    run = client.post(f'/v1/admin/regression-cases/{regression.id}/runs', headers=admin_h,
                      json={'release_version':'0.30.0','result':'passed','details':{'pytest':'pass'}})
    assert run.status_code == 200, run.text
    gate2 = client.post('/v1/admin/release-gate/0.30.0', headers=admin_h)
    assert gate2.json()['decision'] == 'approved'


def test_same_complaint_double_confirm_is_idempotent(client, db_session):
    user_token, _ = _reg(client, 's30-user3@example.com')
    admin_h, _ = _admin(client, db_session, 's30-admin3@example.com')
    r = client.post('/v1/feedback/complaints', headers={'Authorization':f'Bearer {user_token}'}, json=_payload())
    cid = r.json()['id']
    for _ in range(2):
        assert client.post(f'/v1/admin/complaints/{cid}/confirm', headers=admin_h, json={}).status_code == 200
    regression = db_session.scalar(select(RegressionCase))
    assert regression.confirmed_occurrences == 1
    assert regression.release_blocking is False


def test_non_admin_cannot_confirm(client, db_session):
    t1,_ = _reg(client,'s30-user4@example.com'); t2,_ = _reg(client,'s30-user5@example.com')
    r = client.post('/v1/feedback/complaints', headers={'Authorization':f'Bearer {t1}'}, json=_payload())
    out = client.post(f"/v1/admin/complaints/{r.json()['id']}/confirm", headers={'Authorization':f'Bearer {t2}'}, json={})
    assert out.status_code == 403


def test_auto_classification_is_deterministic(client, db_session):
    user_token, _ = _reg(client, 's30-user-auto@example.com')
    payload = _payload(actual='Агент сказал готово, хотя тесты падают')
    payload['category'] = 'auto'; payload['component'] = 'auto'
    r = client.post('/v1/feedback/complaints', headers={'Authorization':f'Bearer {user_token}'}, json=payload)
    assert r.status_code == 201, r.text
    assert r.json()['category'] == 'false_completion'
    assert r.json()['component'] == 'engineering'
