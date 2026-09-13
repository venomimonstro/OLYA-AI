from app.inference.router import choose_route


def test_landing_and_auth_use_unified_light_product_surface(client):
    landing = client.get("/")
    login = client.get("/login")
    register = client.get("/register")

    for response in (landing, login, register):
        assert response.status_code == 200
        assert "OLYA_PRODUCT_SURFACE_V2" in response.text
        assert "color-scheme:light" in response.text
        assert "OLYA AI" in response.text

    assert "От вопроса до готового результата" in landing.text
    assert "30 запросов в день" in landing.text
    assert "form-shell" in login.text


def test_workspace_is_chat_first_and_has_no_visible_fast_mode(client):
    html = client.get("/app").text
    assert "OLYA_PRODUCT_SURFACE_V2" in html
    assert "composer-tools" in html
    assert "chat-attach" in html
    assert "prompt-suggestions" in html
    assert '<option value="fast">Fast</option>' not in html
    assert "Стандарт" in html
    assert "Глубокий" in html
    assert "#nav-files,#view-files" in html
    assert "Fast = 1 единица" not in html


def test_legacy_fast_request_is_server_upgraded_instead_of_serving_low_quality_lane():
    route = choose_route("Ответь коротко, но по существу", "fast", 8192, 8192)
    assert route.mode == "work"
    assert route.max_output_tokens >= 1000
    assert "fast_upgraded_to_auto" in route.reason

    complex_route = choose_route("Проведи аудит безопасности проекта", "fast", 8192, 8192)
    assert complex_route.mode == "deep"
    assert complex_route.reasoning is True


def test_admin_login_matches_light_auth_language_but_remains_separate(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "Вход администратора" in response.text
    assert "background:#fff" in response.text
    assert "/v1/auth/admin/login" in response.text
    assert "/v1/auth/login" not in response.text
