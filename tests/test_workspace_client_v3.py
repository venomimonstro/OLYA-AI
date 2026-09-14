from __future__ import annotations

from scripts.workspace_ux_audit import audit


def test_workspace_v3_contract_audit_passes():
    report = audit()
    assert report["status"] == "passed", report["errors"]
    assert report["simulated_personas"] >= 6


def test_workspace_v3_is_present_on_client_app(client):
    response = client.get("/app")
    assert response.status_code == 200
    html = response.text
    assert "OLYA_WORKSPACE_CLIENT_V3" in html
    assert "--olya-app-height" in html
    assert "jump-bottom" in html
    assert "Простая" in html
    assert "Средняя" in html
    assert "Сложная" in html
    assert "Дополнительные настройки" in html
    assert "Проект / контекст" in html


def test_workspace_v3_preserves_cancel_and_streaming_contracts(client):
    html = client.get("/app").text
    assert "/v1/chat/stream" in html
    assert "/cancel" in html
    assert "AbortController" in html
    assert "stopping?'■':'↑'" in html
    assert "messageObserver.observe" in html
    assert "distanceToBottom()<150" in html


def test_workspace_v3_hides_technical_and_secondary_navigation(client):
    html = client.get("/app").text
    assert "#nav-files,#nav-api{display:none!important}" in html
    assert ".meta{display:none!important}" in html
    assert "#view-files{display:none!important}" in html
    assert "Fast = 1 единица" not in html
