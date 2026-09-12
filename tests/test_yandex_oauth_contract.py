from __future__ import annotations

from pathlib import Path

from app.main import app
from app.services.yandex_oauth import _challenge

ROOT = Path(__file__).resolve().parents[1]


def test_yandex_oauth_routes_are_registered_in_real_app():
    routes = {
        (method, str(getattr(route, "path", "")))
        for route in app.routes
        for method in set(getattr(route, "methods", set()) or set())
    }
    assert ("GET", "/v1/auth/providers") in routes
    assert ("GET", "/v1/auth/yandex/start") in routes
    assert ("GET", "/v1/auth/yandex/callback") in routes
    assert ("GET", "/admin/integrations") in routes


def test_yandex_pkce_s256_matches_rfc7636_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert _challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_yandex_oauth_never_persists_provider_access_token():
    models = (ROOT / "app" / "models_launch.py").read_text("utf-8")
    assert "access_token: Mapped" not in models
    assert "refresh_token: Mapped" not in models
    service = (ROOT / "app" / "services" / "yandex_oauth.py").read_text("utf-8")
    assert "Authorization\": f\"OAuth {access_token}" in service
    assert "state_hash=_digest(state)" in service
    assert "pkce_verifier_ciphertext=encrypt_secret(settings, verifier)" in service
    assert "secrets.compare_digest(state, cookie_state)" in service


def test_yandex_callback_does_not_put_local_session_in_url():
    route = (ROOT / "app" / "api" / "routes" / "yandex_oauth.py").read_text("utf-8")
    assert "sessionStorage.setItem('x1_access_token'" in route
    assert "location.replace('/login?oauth=yandex')" in route
    assert "access_token=" not in route
    assert "httponly=True" in route
    assert 'samesite="lax"' in route


def test_admin_controls_and_public_button_are_toggle_driven():
    owner = (ROOT / "app" / "api" / "routes" / "owner_integrations.py").read_text("utf-8")
    public = (ROOT / "app" / "public_ui.py").read_text("utf-8")
    assert "yandex_oauth_enabled" in owner
    assert "yandex_oauth_client_id" in owner
    assert "yandex_oauth_client_secret" in owner
    assert "/v1/auth/providers" in public
    assert "d?.yandex?.ready" in public
    assert "/v1/auth/yandex/start" in public


def test_yandex_migration_is_linear_after_launch_operations():
    migration = (ROOT / "alembic" / "versions" / "f88b2e7a6c31_add_yandex_oauth.py").read_text("utf-8")
    assert 'revision = "f88b2e7a6c31"' in migration
    assert 'down_revision = "f87a1d9c4e20"' in migration
    assert '"external_auth_identities"' in migration
    assert '"oauth_login_states"' in migration
