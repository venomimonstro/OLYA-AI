from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.main import app
from app.services.http_limits import RequestBodyLimitMiddleware
from scripts.chaos_simulation import _governor_burst
from scripts.download_model import DEFAULT_FILE, DEFAULT_REPO, DEFAULT_SHA256

ROOT = Path(__file__).resolve().parents[1]


def test_application_and_package_remain_at_or_beyond_sprint39():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert app.version == package["project"]["version"]
    assert tuple(map(int, app.version.split(".")[:2])) >= (0, 39)


def test_installer_downloads_real_verified_qwen_and_runs_all_gates():
    script = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    downloader = (ROOT / "scripts" / "download_model.py").read_text("utf-8")
    assert "python3 scripts/download_model.py" in script
    assert "scripts.migrate_legacy_data" not in script
    assert "bash scripts/migrate_legacy_data.sh" in script
    assert "x1-sandbox:0.39" in script
    assert "scripts.sandbox_probe" in script
    assert "--user-journey --chaos" in script
    assert DEFAULT_REPO == "ggml-org/Qwen3.6-35B-A3B-GGUF"
    assert DEFAULT_FILE == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert DEFAULT_SHA256 == "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
    assert "DEFAULT_SHA256" in downloader


def test_bootstrap_can_clone_empty_server_then_delegate_to_installer():
    script = (ROOT / "scripts" / "bootstrap.sh").read_text("utf-8")
    assert "venomimonstro/OLYA-AI.git" in script
    assert "git clone --depth 1 --branch main" in script
    assert 'exec bash "$INSTALL_DIR/scripts/install.sh"' in script


def test_compose_has_private_search_remote_sandbox_and_real_model():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "ghcr.io/searxng/searxng:2026.9.4-15b0c8ef3" in compose
    assert "/models/${X1_LLAMA_MODEL_FILE:-Qwen3.6-35B-A3B-Q4_K_M.gguf}" in compose
    assert "sandbox-worker:" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    app_section = compose.split("\n  app:\n", 1)[1].split("\n  gate:\n", 1)[0]
    assert "/var/run/docker.sock" not in app_section
    assert "./data:/app/data" in app_section
    assert "condition: service_healthy" in app_section


def test_web_app_image_contains_git_and_bounded_http_admission():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "git ca-certificates" in dockerfile
    assert "--limit-concurrency" in dockerfile
    assert "--backlog" in dockerfile
    assert "--workers 1" in dockerfile


def test_remote_sandbox_worker_is_fail_closed():
    worker = (ROOT / "app" / "sandbox_worker_api.py").read_text("utf-8")
    for required in ("--network", '"none"', "--cap-drop=ALL", "no-new-privileges", "--pids-limit", "--memory", "--cpus", "--read-only"):
        assert required in worker
    assert "Only the configured sandbox image is allowed" in worker
    assert "Workspace must be inside code_workspaces" in worker
    assert "Scratch must be inside project_runtimes" in worker
    assert "shell=False" in worker


def test_request_body_limit_rejects_content_length_and_chunked_equivalent():
    inner = FastAPI()

    @inner.post("/body")
    async def body(request: Request):
        data = await request.body()
        return {"size": len(data)}

    limited = RequestBodyLimitMiddleware(inner, max_bytes=16)
    client = TestClient(limited)
    assert client.post("/body", content=b"1234567890123456").status_code == 200
    assert client.post("/body", content=b"12345678901234567").status_code == 413


def test_virtual_100k_model_is_bounded_by_safe_queue():
    result = asyncio.run(_governor_burst(max_queue=64, tasks=256))
    assert result["peak_waiting"] <= 64
    assert result["shed"] > 0
    virtual_users = 100_000
    resident_expensive_requests = 1 + result["max_queue"]
    assert resident_expensive_requests == 65
    assert virtual_users - resident_expensive_requests > 99_000


def test_user_journey_covers_chat_search_and_closed_development():
    script = (ROOT / "scripts" / "e2e_user_journey.py").read_text("utf-8")
    for marker in (
        '"/v1/auth/register"', '"/v1/chat"', '"/v1/research/runs"', '"/discover"', '"/collect"',
        '"/v1/projects"', '"/v1/code/workspaces"', '"/v1/project-runtimes"',
        '"/v1/development-plans/architect-draft"', '"/v1/development-chat"', '"closed_shop_development"',
    ):
        assert marker in script


def test_chaos_suite_covers_security_overload_and_wrong_answer_cases():
    script = (ROOT / "scripts" / "chaos_simulation.py").read_text("utf-8")
    for marker in (
        "protected_route_requires_auth", "client_system_prompt_rejected", "research_ssrf_loopback_rejected",
        "workspace_path_traversal_rejected", "oversized_body_rejected", "auth_bruteforce_throttled",
        "bounded_inference_queue", "virtual_100k_overload_model", "stale_fact_not_marked_supported",
        "sandbox_boundary_available",
    ):
        assert marker in script


def test_release_gate_exposes_sprint39_full_e2e_flags():
    script = (ROOT / "scripts" / "release_gate.py").read_text("utf-8")
    assert '"--user-journey"' in script
    assert '"--chaos"' in script
    assert '"e2e_user_journey"' in script
    assert '"chaos_simulation"' in script
    assert '"format": "x1-release-gate-v4"' in script


def test_legacy_volume_migration_is_copy_only_and_preserves_source():
    script = (ROOT / "scripts" / "migrate_legacy_data.sh").read_text("utf-8")
    assert ":/from:ro" in script
    assert "cp -a /from/. /to/" in script
    assert "docker volume rm" not in script
    assert "Original Docker volume is intentionally preserved" in script
