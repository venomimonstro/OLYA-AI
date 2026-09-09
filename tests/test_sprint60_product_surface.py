from pathlib import Path

from app.user_ui import workspace


ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    response = workspace()
    return response.body.decode("utf-8")


def test_workspace_exposes_one_product_shell():
    html = _html()
    for section in ("Чат", "Проекты", "Файлы", "Изображения", "API", "Аккаунт"):
        assert section in html
    for view in ("view-chat", "view-projects", "view-files", "view-images", "view-api", "view-account"):
        assert f'id="{view}"' in html


def test_workspace_actions_are_wired():
    html = _html()
    expected_ids = {
        "new-chat",
        "project-create",
        "file-upload",
        "studio-open",
        "account-export",
        "logout",
        "send",
    }
    for button_id in expected_ids:
        assert f'id="{button_id}"' in html
        assert f"$('" + button_id + "')" in html


def test_workspace_uses_existing_product_controllers():
    html = _html()
    for endpoint in (
        "/v1/auth/me",
        "/v1/chat/stream",
        "/v1/projects",
        "/v1/images/status",
        "/v1/commerce/usage",
        "/v1/commerce/api-keys",
        "/v1/account/export",
    ):
        assert endpoint in html
    assert "/v1/projects/" in html and "/files" in html


def test_image_surface_is_fail_closed_and_self_hosted_message_is_visible():
    html = _html()
    assert "собственных вычислительных мощностях X1" in html
    assert "studio-open" in html
    assert "button.disabled=!edit.available" in html
    assert "Photo Studio пока недоступна" in html


def test_workspace_preserves_streaming_stop_and_research():
    html = _html()
    assert "text/event-stream" in html
    assert "AbortController" in html
    assert "stopActive" in html
    assert "/v1/research/runs" in html
    assert "Интернет: авто" in html
    assert "Проверка: строгая" in html


def test_workspace_is_mobile_first_and_avoids_model_html_execution():
    html = _html()
    assert "@media(max-width:760px)" in html
    assert "grid-template-columns:1fr" in html
    assert "overflow:auto" in html
    assert ".tablewrap" in html
    assert "innerHTML" not in html
    assert "textContent" in html


def test_workspace_security_headers_remain_strict():
    response = workspace()
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_product_surface_audit_is_part_of_full_regression():
    runner = (ROOT / "scripts" / "run_full_regression.py").read_text("utf-8")
    assert "scripts.product_surface_audit" in runner
    assert "scripts.local_image_contract_audit" in runner
