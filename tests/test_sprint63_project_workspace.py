from pathlib import Path

from app.user_ui import workspace


ROOT = Path(__file__).resolve().parents[1]


def test_project_workspace_aggregates_project_owned_product_state(register_user, client):
    _, headers = register_user("sprint63-owner@example.com")
    created = client.post(
        "/v1/projects",
        headers=headers,
        json={"name": "Launch", "description": "Product launch", "instructions": "Use project facts only"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    conversation = client.post(
        "/v1/conversations",
        headers=headers,
        json={"project_id": project_id, "title": "Launch chat"},
    )
    assert conversation.status_code == 201, conversation.text
    memory = client.put(
        f"/v1/projects/{project_id}/memory",
        headers=headers,
        json={"key": "audience", "value": "small businesses"},
    )
    assert memory.status_code == 200, memory.text
    task = client.post(
        f"/v1/projects/{project_id}/tasks",
        headers=headers,
        json={
            "title": "Prepare launch",
            "goal": "Prepare the release",
            "criteria": [{"text": "Release checklist exists", "verification_method": "manual"}],
        },
    )
    assert task.status_code == 201, task.text

    response = client.get(f"/v1/projects/{project_id}/workspace", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["project"]["id"] == project_id
    assert payload["project"]["role"] == "owner"
    assert payload["counts"] == {"conversations": 1, "files": 0, "memories": 1, "tasks": 1}
    assert payload["recent_conversations"][0]["id"] == conversation.json()["id"]
    assert payload["memories"][0]["key"] == "audience"
    assert payload["recent_tasks"][0]["title"] == "Prepare launch"
    assert payload["development"] is None


def test_project_workspace_masks_cross_tenant_access(register_user, client):
    _, owner_headers = register_user("sprint63-private-owner@example.com")
    project = client.post(
        "/v1/projects",
        headers=owner_headers,
        json={"name": "Private", "description": "secret", "instructions": "secret"},
    ).json()
    _, stranger_headers = register_user("sprint63-stranger@example.com")

    response = client.get(f"/v1/projects/{project['id']}/workspace", headers=stranger_headers)
    assert response.status_code == 404
    assert "secret" not in response.text


def test_project_workspace_ui_is_project_scoped_and_editable_by_role():
    html = workspace().body.decode("utf-8")
    for element_id in (
        "project-workspace",
        "workspace-chat",
        "workspace-files",
        "workspace-title",
        "workspace-description",
        "workspace-instructions",
        "workspace-save",
        "workspace-chats",
        "workspace-memory",
        "workspace-tasks",
        "workspace-development",
    ):
        assert f'id="{element_id}"' in html
    assert "/workspace" in html
    assert "&project_id=" in html
    assert "['owner','manager'].includes(p.role)" in html
    assert "p.instructions||''" in html
    assert "innerHTML" not in html


def test_project_workspace_snapshot_is_bounded_and_avoids_heavy_content():
    source = (ROOT / "app/services/project_workspace.py").read_text("utf-8")
    assert "PREVIEW_LIMIT = 8" in source
    assert "MEMORY_PREVIEW_LIMIT = 50" in source
    assert ".limit(PREVIEW_LIMIT)" in source
    assert "ProjectFile.is_current.is_(True)" in source
    assert "FileChunk" not in source
    assert "Message" not in source


def test_project_workspace_is_a_release_critical_product_route():
    audit = (ROOT / "scripts/product_surface_audit.py").read_text("utf-8")
    assert '"/v1/projects/{project_id}/workspace"' in audit
