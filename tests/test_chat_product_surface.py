from types import SimpleNamespace

from fastapi import Request

from app.services.auth import _expensive_public_request
from app.services.onboarding import onboarding_payload


def _request(path: str, method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def test_core_chat_is_not_progressive_rollout_gated():
    assert _expensive_public_request(_request("/v1/chat")) is False
    assert _expensive_public_request(_request("/v1/chat/stream")) is False
    assert _expensive_public_request(_request("/v1/chat/runs/request-1")) is False
    assert _expensive_public_request(_request("/v1/images/generations")) is True


def test_onboarding_never_hijacks_successful_login():
    row = SimpleNamespace(
        show_again=True,
        completed_at=None,
        dismissed_at=None,
        started_at=None,
        reopened_at=None,
        first_chat_at=None,
        first_successful_answer_at=None,
        first_project_at=None,
        first_file_at=None,
    )
    payload = onboarding_payload(row)
    assert payload["visible"] is False
    assert payload["recommended_action"] == "chat"


def test_workspace_renders_chat_management(client):
    response = client.get("/app")
    assert response.status_code == 200, response.text
    html = response.text
    assert 'id="project-tree"' in html
    assert 'id="quick-project-create"' in html
    assert "all_projects=true" in html
    assert "renameConversation" in html
    assert "moveConversationDialog" in html
    assert "deleteConversation" in html


def test_conversation_crud_move_and_project_delete_preserves_chat(client, register_user):
    _data, headers = register_user("chat-manager@example.com")

    p1 = client.post("/v1/projects", headers=headers, json={"name": "Проект 1"})
    p2 = client.post("/v1/projects", headers=headers, json={"name": "Проект 2"})
    assert p1.status_code == 201, p1.text
    assert p2.status_code == 201, p2.text
    project1 = p1.json()["id"]
    project2 = p2.json()["id"]

    created = client.post(
        "/v1/conversations",
        headers=headers,
        json={"title": "Черновик", "project_id": project1},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    renamed = client.patch(
        f"/v1/conversations/{conversation_id}",
        headers=headers,
        json={"title": "Новый заголовок", "project_id": project2},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Новый заголовок"
    assert renamed.json()["project_id"] == project2

    all_rows = client.get("/v1/conversations?all_projects=true&limit=100", headers=headers)
    assert all_rows.status_code == 200, all_rows.text
    assert any(row["id"] == conversation_id and row["project_id"] == project2 for row in all_rows.json())

    deleted_project = client.delete(f"/v1/projects/{project2}", headers=headers)
    assert deleted_project.status_code == 204, deleted_project.text

    all_rows = client.get("/v1/conversations?all_projects=true&limit=100", headers=headers)
    preserved = next(row for row in all_rows.json() if row["id"] == conversation_id)
    assert preserved["project_id"] is None

    removed = client.delete(f"/v1/conversations/{conversation_id}", headers=headers)
    assert removed.status_code == 204, removed.text
    all_rows = client.get("/v1/conversations?all_projects=true&limit=100", headers=headers)
    assert all(row["id"] != conversation_id for row in all_rows.json())


def test_user_cannot_manage_another_users_chat(client, register_user):
    _owner, owner_headers = register_user("owner-chat@example.com")
    _other, other_headers = register_user("other-chat@example.com")
    created = client.post("/v1/conversations", headers=owner_headers, json={"title": "Приватный чат"})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    rename = client.patch(
        f"/v1/conversations/{conversation_id}",
        headers=other_headers,
        json={"title": "Чужое имя"},
    )
    delete = client.delete(f"/v1/conversations/{conversation_id}", headers=other_headers)
    assert rename.status_code == 404
    assert delete.status_code == 404
