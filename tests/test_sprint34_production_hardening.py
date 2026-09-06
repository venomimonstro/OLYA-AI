from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.main import app
from app.schemas.chat import ChatMessage
from app.services import system_observability as observability
from app.services.code_workspace import WorkspaceError, run_command
from app.services.context import ContextCompiler
from app.services.resource_governor import ResourceBusyError, ResourceGovernor
from app.services.sandbox import SandboxBackendInfo, _base_run_args
from app.services.source_context import FRESHNESS_SENTINEL, SourceContextBuilder


def test_real_application_route_contract_is_stable():
    result = observability._route_contract(app)
    assert result["status"] == observability.STABLE, result


def test_context_compiler_preserves_repeated_dialogue_turns():
    compiler = ContextCompiler(max_chars=10_000)
    messages = [ChatMessage(role="user", content="Повтори проверку"),ChatMessage(role="assistant", content="Проверил"),ChatMessage(role="user", content="Повтори проверку")]
    compiled = compiler.compile(messages)
    assert [item.content for item in compiled if item.role == "user"].count("Повтори проверку") == 2


def test_deep_budget_retains_more_context_than_small_budget():
    compiler = ContextCompiler(max_chars=100_000)
    messages = [ChatMessage(role="user", content=f"turn-{index}-" + "x" * 1500) for index in range(30)]
    work = compiler.compile(messages, max_chars=12_000); deep = compiler.compile(messages, max_chars=36_000)
    assert sum(len(item.content) for item in deep) > sum(len(item.content) for item in work)
    assert len(deep) > len(work)


def test_queue_wait_has_hard_timeout():
    async def scenario():
        governor = ResourceGovernor(max_concurrent=1, max_queue=2, wait_timeout_seconds=0.03)
        entered = asyncio.Event(); release = asyncio.Event()
        async def holder():
            async with governor.slot(): entered.set(); await release.wait()
        task=asyncio.create_task(holder()); await entered.wait()
        with pytest.raises(ResourceBusyError):
            async with governor.slot(): pass
        release.set(); await task
    asyncio.run(scenario())


def test_host_static_runner_rejects_mypy_and_workspace_escape(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError): run_command(tmp_path, ["mypy", "ok.py"], 5, allow_unsafe=False)
    with pytest.raises(WorkspaceError): run_command(tmp_path, ["python", "-m", "py_compile", "../outside.py"], 5, allow_unsafe=False)


def test_restricted_sandbox_is_network_none(monkeypatch, tmp_path: Path):
    image="x1-sandbox:test"; info=SandboxBackendInfo("docker","/usr/bin/docker",True)
    monkeypatch.setattr("app.services.sandbox._image_exists", lambda *_args, **_kwargs: True)
    args=_base_run_args(info,image=image,workspace=tmp_path/"workspace",scratch=tmp_path/"scratch",cpu_limit=1,memory_mb=256,process_limit=32,network_policy="restricted")
    assert "--network" in args and args[args.index("--network")+1] == "none"


def test_freshness_request_without_sources_gets_fail_closed_policy():
    messages,verified=SourceContextBuilder().build(None,None,[],"Какая актуальная цена сейчас?",current_project_id=None)
    assert FRESHNESS_SENTINEL in verified
    assert messages and messages[0].role == "system" and "Do not present" in messages[0].content


def test_compose_persists_data_matches_model_and_keeps_socket_out_of_web_app():
    root=Path(__file__).resolve().parents[1]; compose=(root/"docker-compose.yml").read_text("utf-8")
    app_section=compose.split("\n  app:\n",1)[1].split("\n  gate:\n",1)[0]
    assert "./data:/app/data" in app_section
    assert "/var/run/docker.sock" not in app_section
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}" in compose
    assert '${X1_DEEP_CONTEXT_TOKENS:-8192}' in compose
    assert "/models/Qwen3-30B-A3B-Q4_K_M.gguf" in compose
    assert "sandbox-worker:" in compose and "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert "scripts.image_worker" in compose


def test_backup_archives_authoritative_host_data_and_is_atomic():
    root=Path(__file__).resolve().parents[1]; script=(root/"scripts"/"backup.sh").read_text("utf-8")
    assert 'DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}"' in script
    assert "os.walk(root, followlinks=False)" in script
    assert "unsupported data member for safe backup" in script
    assert "SHA256SUMS" in script and ".partial.$$" in script
    # Update flow stops app before backup; backup therefore must not depend on
    # executing inside the stopped web container.
    assert "docker compose exec -T app" not in script
