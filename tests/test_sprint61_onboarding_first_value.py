from pathlib import Path

from sqlalchemy import func, select

from app.models import ProductEvent, Project, UserOnboarding


ROOT = Path(__file__).resolve().parents[1]


def test_new_account_gets_server_owned_onboarding(register_user, client, db_session):
    data, headers = register_user("onboarding-new@example.com", display_name="New User")

    response = client.get("/v1/account/onboarding", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["visible"] is True
    assert payload["completed"] is False
    assert payload["recommended_action"] == "chat"
    assert payload["milestones"] == {
        "chat_started": None,
        "successful_answer": None,
        "project_created": None,
        "file_uploaded": None,
    }

    row = db_session.get(UserOnboarding, data["user_id"])
    assert row is not None
    assert db_session.scalar(
        select(func.count()).select_from(ProductEvent).where(
            ProductEvent.user_id == data["user_id"], ProductEvent.event_name == "registered"
        )
    ) == 1


def test_onboarding_reconciles_real_project_and_completes(register_user, client, db_session):
    data, headers = register_user("onboarding-project@example.com")
    assert client.get("/v1/account/onboarding", headers=headers).status_code == 200

    created = client.post(
        "/v1/projects",
        headers=headers,
        json={"name": "First project", "description": "", "instructions": ""},
    )
    assert created.status_code == 201, created.text

    response = client.get("/v1/account/onboarding", headers=headers)
    payload = response.json()
    assert payload["completed"] is True
    assert payload["visible"] is False
    assert payload["milestones"]["project_created"] is not None

    event_count = db_session.scalar(
        select(func.count()).select_from(ProductEvent).where(
            ProductEvent.user_id == data["user_id"], ProductEvent.event_name == "first_project_created"
        )
    )
    assert event_count == 1

    # Reconciliation is idempotent and must not inflate analytics rows.
    assert client.get("/v1/account/onboarding", headers=headers).status_code == 200
    assert db_session.scalar(
        select(func.count()).select_from(ProductEvent).where(
            ProductEvent.user_id == data["user_id"], ProductEvent.event_name == "first_project_created"
        )
    ) == 1


def test_dismiss_and_reopen_are_server_persisted(register_user, client):
    _, headers = register_user("onboarding-reopen@example.com")

    dismissed = client.post("/v1/account/onboarding/dismiss", headers=headers)
    assert dismissed.status_code == 200
    assert dismissed.json()["visible"] is False
    assert dismissed.json()["dismissed"] is True

    reopened = client.post("/v1/account/onboarding/reopen", headers=headers)
    assert reopened.status_code == 200
    assert reopened.json()["visible"] is True
    assert reopened.json()["show_again"] is True

    reloaded = client.get("/v1/account/onboarding", headers=headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["visible"] is True


def test_welcome_page_is_registered_and_private(client):
    response = client.get("/welcome")
    assert response.status_code == 200
    assert "Получите первый результат в X1" in response.text
    assert "/v1/account/onboarding" in response.text
    assert "/v1/projects" in response.text
    assert "files?filename=" in response.text
    assert "noindex" in response.headers.get("x-robots-tag", "")
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp


def test_auth_ui_uses_server_onboarding_destination(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert "async function destination(token)" in response.text
    assert "/v1/account/onboarding" in response.text
    assert "return '/welcome'" in response.text


def test_sprint61_migration_is_linear_and_models_are_registered():
    migration = (ROOT / "alembic/versions/f61b0a4d2c90_add_onboarding_first_value.py").read_text("utf-8")
    registry = (ROOT / "app/models.py").read_text("utf-8")
    assert 'revision = "f61b0a4d2c90"' in migration
    assert 'down_revision = "f60a93c7d511"' in migration
    assert '"user_onboarding"' in migration
    assert '"product_events"' in migration
    assert "from app.models_sprint61 import *" in registry


def test_onboarding_reconciliation_reads_authoritative_product_facts():
    source = (ROOT / "app/services/onboarding.py").read_text("utf-8")
    assert "Conversation.owner_id == user.id" in source
    assert "UsageEvent.user_id == user.id" in source
    assert "UsageEvent.success.is_(True)" in source
    assert "Project.owner_id == user.id" in source
    assert "ProjectFile.uploaded_by == user.id" in source
    assert 'ProjectFile.status == "ready"' in source
    assert "onboarding_completed" in source
