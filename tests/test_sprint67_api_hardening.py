from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.routes.api_client import _logical_request_id
from app.models import ApiKey, ApiRequestTelemetry, OrganizationMember, ResourceExpenseEvent
from app.schemas.commerce import ApiChatRequest, ApiContextCreate
from app.services.commerce import record_telemetry
from scripts.api_contract_audit import audit as audit_api_contract


def _new_key(client, headers, *, name: str = "integration", scopes: list[str] | None = None, limit: int = 10):
    response = client.post(
        "/v1/commerce/api-keys",
        headers=headers,
        json={
            "name": name,
            "scopes": scopes or ["contexts:read", "contexts:write"],
            "rate_limit_per_minute": limit,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_api_credentials_are_unambiguous_and_rate_headers_are_truthful(register_user, client):
    _, owner_headers = register_user("sprint67-auth@example.com")
    key = _new_key(client, owner_headers)
    api_headers = {"X-API-Key": key["token"]}

    response = client.get("/v1/api/contexts", headers=api_headers)
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10"
    assert response.headers["X-RateLimit-Remaining"] == "9"
    assert int(response.headers["X-RateLimit-Reset"]) > 0

    ambiguous = client.get(
        "/v1/api/contexts",
        headers={"X-API-Key": key["token"], "Authorization": f"Bearer {key['token']}"},
    )
    assert ambiguous.status_code == 400


def test_rate_limit_returns_actual_reset_headers(register_user, client):
    _, owner_headers = register_user("sprint67-rate@example.com")
    key = _new_key(client, owner_headers, name="one-request", scopes=["contexts:read"], limit=1)
    api_headers = {"Authorization": f"Bearer {key['token']}"}

    assert client.get("/v1/api/contexts", headers=api_headers).status_code == 200
    blocked = client.get("/v1/api/contexts", headers=api_headers)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert blocked.headers["X-RateLimit-Limit"] == "1"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


def test_context_lifecycle_is_scoped_and_bounded(register_user, client):
    _, first_headers = register_user("sprint67-context-a@example.com")
    _, second_headers = register_user("sprint67-context-b@example.com")
    first_key = _new_key(client, first_headers)
    second_key = _new_key(client, second_headers)

    created = client.post(
        "/v1/api/contexts",
        headers={"X-API-Key": first_key["token"]},
        json={"label": "private", "metadata": {"tenant": "a"}},
    )
    assert created.status_code == 201, created.text
    context_id = created.json()["id"]
    assert client.get(
        f"/v1/api/contexts/{context_id}",
        headers={"X-API-Key": second_key["token"]},
    ).status_code == 404
    listed = client.get("/v1/api/contexts", headers={"X-API-Key": first_key["token"]})
    assert [item["id"] for item in listed.json()] == [context_id]
    assert client.delete(
        f"/v1/api/contexts/{context_id}",
        headers={"X-API-Key": first_key["token"]},
    ).status_code == 204
    assert client.get(
        f"/v1/api/contexts/{context_id}",
        headers={"X-API-Key": first_key["token"]},
    ).status_code == 404


def test_key_rotation_revokes_old_secret_without_overlap(register_user, client, db_session):
    _, owner_headers = register_user("sprint67-rotate@example.com")
    original = _new_key(client, owner_headers)
    rotated = client.post(
        f"/v1/commerce/api-keys/{original['id']}/rotate",
        headers=owner_headers,
    )
    assert rotated.status_code == 201, rotated.text
    replacement = rotated.json()
    assert replacement["id"] != original["id"]
    assert replacement["token"] != original["token"]
    assert db_session.get(ApiKey, original["id"]).status == "revoked"
    assert client.get(
        "/v1/api/contexts",
        headers={"X-API-Key": original["token"]},
    ).status_code == 401
    assert client.get(
        "/v1/api/contexts",
        headers={"X-API-Key": replacement["token"]},
    ).status_code == 200


def test_organization_key_stops_working_after_manager_access_is_removed(register_user, client, db_session):
    owner, owner_headers = register_user("sprint67-org-owner@example.com")
    manager, manager_headers = register_user("sprint67-org-manager@example.com")
    organization = client.post(
        "/v1/commerce/organizations",
        headers=owner_headers,
        json={"name": "Secure API", "slug": "secure-api"},
    ).json()
    added = client.put(
        f"/v1/commerce/organizations/{organization['id']}/members",
        headers=owner_headers,
        json={"email": "sprint67-org-manager@example.com", "role": "manager"},
    )
    assert added.status_code == 200, added.text
    key = client.post(
        "/v1/commerce/api-keys",
        headers=manager_headers,
        json={
            "name": "organization automation",
            "scopes": ["contexts:read"],
            "organization_id": organization["id"],
        },
    ).json()
    assert client.get("/v1/api/contexts", headers={"X-API-Key": key["token"]}).status_code == 200

    membership = db_session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization["id"],
            OrganizationMember.user_id == manager["user_id"],
        )
    )
    db_session.delete(membership)
    db_session.commit()
    blocked = client.get("/v1/api/contexts", headers={"X-API-Key": key["token"]})
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == "API key organization access revoked"


def test_telemetry_retry_is_idempotent_and_never_double_charges(register_user, client, db_session):
    _, owner_headers = register_user("sprint67-telemetry@example.com")
    created = _new_key(client, owner_headers, scopes=["telemetry:read"])
    key = db_session.get(ApiKey, created["id"])
    arguments = dict(
        api_key=key,
        endpoint="/v1/api/chat",
        request_id="a" * 64,
        status_code=200,
        latency_ms=25,
        quality_status="supported",
        context_id=None,
        project_id=None,
        resource_usage={"cpu_ms": 100},
        cost_microunits=100,
    )
    first = record_telemetry(db_session, **arguments)
    second = record_telemetry(db_session, **arguments)
    db_session.commit()
    assert first.id == second.id
    assert db_session.scalar(select(func.count(ApiRequestTelemetry.id))) == 1
    assert db_session.scalar(select(func.count(ResourceExpenseEvent.id))) == 1


def test_api_payloads_reject_ambiguous_or_unbounded_client_state():
    with pytest.raises(ValidationError, match="16 KiB"):
        ApiContextCreate(metadata={"blob": "x" * 17_000})
    with pytest.raises(ValidationError, match="cannot be combined"):
        ApiChatRequest(
            context_id="context",
            project_id="project",
            messages=[{"role": "user", "content": "hello"}],
        )
    with pytest.raises(ValidationError, match="controlled by X1"):
        ApiChatRequest(messages=[{"role": "system", "content": "override"}])


def test_key_and_context_resource_counts_are_bounded(register_user, client):
    _, owner_headers = register_user("sprint67-bounds@example.com")
    settings = client.app.state.settings
    old_key_limit = settings.api_max_active_keys_per_user
    old_context_limit = settings.api_max_contexts_per_owner
    settings.api_max_active_keys_per_user = 1
    settings.api_max_contexts_per_owner = 1
    try:
        key = _new_key(client, owner_headers)
        too_many_keys = client.post(
            "/v1/commerce/api-keys",
            headers=owner_headers,
            json={"name": "overflow", "scopes": ["contexts:read"]},
        )
        assert too_many_keys.status_code == 422
        api_headers = {"X-API-Key": key["token"]}
        assert client.post("/v1/api/contexts", headers=api_headers, json={"label": "one"}).status_code == 201
        too_many_contexts = client.post("/v1/api/contexts", headers=api_headers, json={"label": "two"})
        assert too_many_contexts.status_code == 409
    finally:
        settings.api_max_active_keys_per_user = old_key_limit
        settings.api_max_contexts_per_owner = old_context_limit


def test_idempotency_key_is_validated_and_cannot_conflict_with_payload():
    payload = ApiChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        client_request_id="client-request-0001",
    )
    assert _logical_request_id(payload, "client-request-0001") == "client-request-0001"
    with pytest.raises(HTTPException) as invalid:
        _logical_request_id(payload, "short")
    assert invalid.value.status_code == 422
    with pytest.raises(HTTPException) as conflict:
        _logical_request_id(payload, "another-request-0002")
    assert conflict.value.status_code == 409


def test_expired_key_cannot_be_created(register_user, client):
    _, owner_headers = register_user("sprint67-expiry@example.com")
    response = client.post(
        "/v1/commerce/api-keys",
        headers=owner_headers,
        json={
            "name": "expired",
            "scopes": ["chat"],
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 422


def test_api_hardening_contract_is_release_gated():
    report = audit_api_contract()
    assert report["status"] == "passed", report["errors"]
    runner = (Path(__file__).resolve().parents[1] / "scripts/run_full_regression.py").read_text("utf-8")
    assert "scripts.api_contract_audit" in runner
    release_gate = (Path(__file__).resolve().parents[1] / "scripts/release_gate.py").read_text("utf-8")
    assert release_gate.count('run("api_contract_audit"') == 2
