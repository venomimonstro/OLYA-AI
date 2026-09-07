from __future__ import annotations

import tomllib
from pathlib import Path

from app.main import app
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
    # Model/source text is inserted as text, never interpreted as arbitrary HTML.
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
