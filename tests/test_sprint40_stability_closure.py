from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.main import app
from app.models import AuthSession
from app.schemas.chat import ChatResponse, ChatUsage

ROOT = Path(__file__).resolve().parents[1]


def test_application_package_and_workspace_are_sprint40():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    paths = {getattr(route, "path", "") for route in app.routes}
    assert package["project"]["version"] == app.version == "0.40.0"
    assert "/app" in paths


def test_chat_response_exposes_canonical_conversation_id():
    response = ChatResponse(
        text="ok",
        model="qwen",
        usage=ChatUsage(raw_message_chars=2, compiled_message_chars=2, mode="fast"),
        conversation_id="conversation-1",
    )
    assert response.conversation_id == "conversation-1"
    route = (ROOT / "app" / "api" / "routes" / "chat.py").read_text("utf-8")
    assert "conversation_id=conversation.id" in route


def test_auth_onboarding_enters_real_user_workspace():
    public = (ROOT / "app" / "public_ui.py").read_text("utf-8")
    workspace = (ROOT / "app" / "user_ui.py").read_text("utf-8")
    assert "location.assign('/app')" in public
    assert "location.replace('/app')" in public
    for marker in (
        "'/v1/conversations'",
        "'/v1/research/runs'",
        "'/v1/chat'",
        "research_source_ids",
        "conversation_id:cid",
        "Интернет: авто",
        "Поиск не дал проверяемых источников",
    ):
        assert marker in workspace
    assert "b.textContent=text" in workspace
    assert ".innerHTML" not in workspace


def test_workspace_is_private_and_not_indexable():
    main = (ROOT / "app" / "main.py").read_text("utf-8")
    workspace = (ROOT / "app" / "user_ui.py").read_text("utf-8")
    assert '"/app"' in main
    assert "Disallow: /app" in main
    assert '"Cache-Control": "no-store"' in workspace
    assert "Content-Security-Policy" in workspace
    assert "frame-ancestors 'none'" in workspace


def test_compose_enforces_memory_limits_in_non_swarm_mode():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    for marker in (
        "mem_limit: 1536m",
        "mem_limit: 768m",
        "mem_limit: 384m",
        "mem_limit: 2048m",
        "mem_limit: 4096m",
        "mem_limit: ${X1_LLAMA_MEMORY_LIMIT:-22g}",
    ):
        assert marker in compose


def test_installer_and_doctor_reserve_host_memory_outside_llama():
    install = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    doctor = (ROOT / "scripts" / "doctor.py").read_text("utf-8")
    env = (ROOT / ".env.example").read_text("utf-8")
    assert "ram_gb >= 30" in install
    assert "llama_memory_gb=$((ram_gb - 8))" in install
    assert "llama_memory_gb > 24" in install
    assert "X1_LLAMA_MEMORY_LIMIT" in install
    assert "host_memory_budget" in doctor
    assert "required_non_llama_reserve_gib" in doctor
    assert "X1_LLAMA_MEMORY_LIMIT=22g" in env


def test_auth_session_growth_is_bounded_and_logout_all_revokes_everything(client, db_session):
    email = "session-cap@example.invalid"
    password = "session-cap-password-123"
    created = client.post("/v1/auth/register", json={"email": email, "password": password, "display_name": "Sessions"})
    assert created.status_code == 201, created.text
    latest_token = created.json()["access_token"]

    # Create substantially more live tokens than the default allowed window.
    for _ in range(24):
        login = client.post("/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        latest_token = login.json()["access_token"]

    now = datetime.now(timezone.utc)
    active = int(
        db_session.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        )
        or 0
    )
    assert active <= 20

    logout = client.post("/v1/auth/logout-all", headers={"Authorization": f"Bearer {latest_token}"})
    assert logout.status_code == 204, logout.text
    db_session.expire_all()
    remaining = int(
        db_session.scalar(select(func.count(AuthSession.id)).where(AuthSession.revoked_at.is_(None))) or 0
    )
    assert remaining == 0
