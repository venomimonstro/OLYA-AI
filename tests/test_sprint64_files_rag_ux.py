from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import ProjectFile
from app.services.file_citations import citations_from_answer
from app.user_ui import workspace


ROOT = Path(__file__).resolve().parents[1]


def _project(register_user, client, email: str):
    _, headers = register_user(email)
    response = client.post(
        "/v1/projects",
        headers=headers,
        json={"name": "Files", "description": "", "instructions": ""},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], headers


def _upload(client, headers, project_id: str, filename: str, content: bytes, logical_name: str | None = None):
    query = f"?filename={filename}"
    if logical_name:
        query += f"&logical_name={logical_name}"
    return client.post(
        f"/v1/projects/{project_id}/files{query}",
        headers={**headers, "Content-Type": "text/plain"},
        content=content,
    )


def test_file_version_history_current_selection_and_delete(register_user, client):
    project_id, headers = _project(register_user, client, "sprint64-versions@example.com")
    first = _upload(client, headers, project_id, "brief.txt", b"first release facts", "brief.txt")
    second = _upload(client, headers, project_id, "brief.txt", b"second release facts", "brief.txt")
    assert first.status_code == second.status_code == 201
    assert first.json()["status"] == second.json()["status"] == "ready"
    assert second.json()["version"] == 2

    history = client.get(f"/v1/projects/{project_id}/files?include_history=true", headers=headers).json()
    assert [(row["version"], row["is_current"]) for row in history] == [(2, True), (1, False)]
    restored = client.post(
        f"/v1/projects/{project_id}/files/{first.json()['id']}/make-current",
        headers=headers,
    )
    assert restored.status_code == 200
    assert restored.json()["is_current"] is True
    deleted = client.delete(f"/v1/projects/{project_id}/files/{first.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    current = client.get(f"/v1/projects/{project_id}/files", headers=headers).json()
    assert current[0]["id"] == second.json()["id"]
    assert current[0]["is_current"] is True


def test_failed_file_is_visible_retryable_and_not_current(register_user, client):
    project_id, headers = _project(register_user, client, "sprint64-retry@example.com")
    response = _upload(client, headers, project_id, "payload.bin", b"not a supported document")
    assert response.status_code == 201
    failed = response.json()
    assert failed["status"] == "error"
    assert failed["is_current"] is False
    assert failed["error_message"] == "Unsupported file type"

    retry = client.post(f"/v1/projects/{project_id}/files/{failed['id']}/retry", headers=headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "error"
    assert retry.json()["id"] == failed["id"]
    assert client.post(
        f"/v1/projects/{project_id}/files?filename=empty.txt",
        headers={**headers, "Content-Type": "text/plain"},
        content=b"",
    ).status_code == 400


def test_retry_version_selection_and_delete_are_manager_only(register_user, client):
    project_id, owner_headers = _project(register_user, client, "sprint64-owner@example.com")
    _, viewer_headers = register_user("sprint64-viewer@example.com")
    added = client.put(
        f"/v1/projects/{project_id}/members",
        headers=owner_headers,
        json={"email": "sprint64-viewer@example.com", "role": "viewer"},
    )
    assert added.status_code == 200
    failed = _upload(client, owner_headers, project_id, "blocked.bin", b"unsupported").json()
    assert client.post(
        f"/v1/projects/{project_id}/files/{failed['id']}/retry",
        headers=viewer_headers,
    ).status_code == 403
    assert client.post(
        f"/v1/projects/{project_id}/files/{failed['id']}/make-current",
        headers=viewer_headers,
    ).status_code == 403
    assert client.delete(
        f"/v1/projects/{project_id}/files/{failed['id']}",
        headers=viewer_headers,
    ).status_code == 403


def test_stale_processing_recovers_to_retryable_error(register_user, client, db_session):
    project_id, headers = _project(register_user, client, "sprint64-stale@example.com")
    response = _upload(client, headers, project_id, "stale.txt", b"recoverable content")
    file = db_session.get(ProjectFile, response.json()["id"])
    file.status = "processing"
    file.is_current = False
    file.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.commit()

    rows = client.get(f"/v1/projects/{project_id}/files?include_history=true", headers=headers).json()
    assert rows[0]["status"] == "error"
    assert "Retry" in rows[0]["error_message"]


def test_chat_file_citations_are_resolved_from_database(register_user, client, db_session):
    project_id, headers = _project(register_user, client, "sprint64-citations@example.com")
    uploaded = _upload(client, headers, project_id, "source.txt", b"The launch date is 18 October.").json()
    answer = f"Launch is 18 October FILE_REF[file_id={uploaded['id']};name=forged;version=999;chunk=0]"
    evidence = f"FILE_REF[file_id={uploaded['id']};name=source.txt;version=1;chunk=0]"
    citations = citations_from_answer(db_session, project_id, answer, evidence)
    assert len(citations) == 1
    assert citations[0].logical_name == "source.txt"
    assert citations[0].version == 1
    assert "18 October" in citations[0].excerpt
    assert citations_from_answer(db_session, "another-project", answer, evidence) == []
    assert citations_from_answer(db_session, project_id, answer, "no file evidence") == []


def test_files_ui_has_status_history_actions_and_safe_citations():
    html = workspace().body.decode("utf-8")
    for marker in ("files-history", "include_history=true", "/retry", "/make-current", "file_citations"):
        assert marker in html
    assert "Обработка" in html
    assert "Сделать текущей" in html
    assert "innerHTML" not in html


def test_file_lifecycle_routes_are_release_critical():
    audit = (ROOT / "scripts/product_surface_audit.py").read_text("utf-8")
    assert '"/v1/projects/{project_id}/files/{file_id}/retry"' in audit
    assert '"/v1/projects/{project_id}/files/{file_id}/make-current"' in audit
