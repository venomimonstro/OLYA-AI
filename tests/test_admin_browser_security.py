from app.models import User
from app.services.admin_browser_session import ADMIN_BROWSER_COOKIE
from app.services.auth import hash_password


def _create_admin(db_session, *, email: str = "owner@example.test", password: str = "owner-password-123") -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name="Owner",
        is_admin=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_owner_dashboard_redirects_anonymous_browser_to_admin_login(client):
    response = client.get("/admin/owner", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?next=/admin/owner")
    assert "no-store" in response.headers.get("cache-control", "")


def test_admin_login_page_is_public_but_admin_pages_are_not(client):
    login_page = client.get("/admin/login", follow_redirects=False)
    assert login_page.status_code == 200
    assert "Вход администратора" in login_page.text
    assert "/v1/auth/admin/login" in login_page.text

    protected = client.get("/admin", follow_redirects=False)
    assert protected.status_code == 303


def test_normal_login_does_not_unlock_admin_surface(client, db_session):
    _create_admin(db_session)
    login = client.post(
        "/v1/auth/login",
        json={"email": "owner@example.test", "password": "owner-password-123"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["is_admin"] is True
    assert ADMIN_BROWSER_COOKIE not in login.headers.get("set-cookie", "")

    page = client.get("/admin/owner", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"].startswith("/admin/login")


def test_dedicated_admin_login_issues_http_only_cookie_and_unlocks_page(client, db_session):
    _create_admin(db_session)
    login = client.post(
        "/v1/auth/admin/login",
        json={"email": "owner@example.test", "password": "owner-password-123"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["is_admin"] is True
    set_cookie = login.headers.get("set-cookie", "")
    assert ADMIN_BROWSER_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/admin" in set_cookie

    page = client.get("/admin/owner", follow_redirects=False)
    assert page.status_code == 200
    assert "Административное меню" in page.text
    assert "Admin Bearer token" not in page.text


def test_non_admin_cannot_use_dedicated_admin_login(client, register_user):
    register_user("member@example.test", password="very-secure-password")
    response = client.post(
        "/v1/auth/admin/login",
        json={"email": "member@example.test", "password": "very-secure-password"},
    )
    assert response.status_code == 401
    assert ADMIN_BROWSER_COOKIE not in client.cookies


def test_logout_revokes_owner_browser_gate(client, db_session):
    _create_admin(db_session, email="logout-owner@example.test")
    login = client.post(
        "/v1/auth/admin/login",
        json={"email": "logout-owner@example.test", "password": "owner-password-123"},
    )
    token = login.json()["access_token"]
    assert client.get("/admin/owner", follow_redirects=False).status_code == 200

    logout = client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 204
    assert client.get("/admin/owner", follow_redirects=False).status_code == 303
