from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import _production_configuration_errors, app
from app.services.quality import AnswerQualityEngine
from app.services.source_context import FRESHNESS_SENTINEL, SourceContextBuilder
import app.sandbox_worker_api as sandbox_worker

ROOT = Path(__file__).resolve().parents[1]


class SourceDB:
    def __init__(self, source):
        self.source = source

    def get(self, model, key):
        _ = model
        return self.source if key == self.source.id else None


class MultiSourceDB:
    def __init__(self, *sources):
        self.sources = {source.id: source for source in sources}

    def get(self, model, key):
        _ = model
        return self.sources.get(key)


def _source(*, age_hours: float, content: str = "Bitcoin current price market data", source_id: str = "source-1", url: str = "https://example.com/market"):
    return SimpleNamespace(
        id=source_id,
        status="ready",
        project_id=None,
        user_id="user-1",
        title="Market source",
        url=url,
        final_url=url,
        content=content,
        content_sha256="a" * 64,
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


def test_stale_research_cannot_verify_current_fact():
    source = _source(age_hours=48)
    messages, verified = SourceContextBuilder().build(
        SourceDB(source),
        SimpleNamespace(id="user-1"),
        [source.id],
        "Какая текущая цена Bitcoin сейчас?",
        freshness_max_age_seconds=3600,
        freshness_min_independent_hosts=1,
    )
    assert FRESHNESS_SENTINEL in verified
    assert source.final_url not in verified
    supplied = "\n".join(message.content for message in messages)
    assert "STALE" in supplied
    assert "outside the current-fact freshness window" in supplied


def test_fresh_relevant_research_can_verify_current_fact():
    source = _source(age_hours=0.2)
    messages, verified = SourceContextBuilder().build(
        SourceDB(source),
        SimpleNamespace(id="user-1"),
        [source.id],
        "Какая текущая цена Bitcoin сейчас?",
        freshness_max_age_seconds=3600,
        freshness_min_independent_hosts=1,
    )
    assert FRESHNESS_SENTINEL in verified
    assert source.final_url in verified
    assert any("ELIGIBLE" in message.content for message in messages)


def test_irrelevant_source_is_not_promoted_to_verified_evidence():
    source = _source(age_hours=0.1, content="Completely unrelated cooking recipe text")
    _, verified = SourceContextBuilder().build(
        SourceDB(source),
        SimpleNamespace(id="user-1"),
        [source.id],
        "Какая текущая цена Bitcoin сейчас?",
        freshness_max_age_seconds=3600,
        freshness_min_independent_hosts=1,
    )
    assert source.final_url not in verified


def test_only_sources_actually_selected_into_prompt_are_verified():
    primary = _source(
        age_hours=0.1,
        source_id="source-primary",
        url="https://example.com/primary",
        content="Bitcoin current price market data Bitcoin current price market data",
    )
    secondary = _source(
        age_hours=0.1,
        source_id="source-secondary",
        url="https://example.com/secondary",
        content="Bitcoin market background",
    )
    messages, verified = SourceContextBuilder(max_excerpts=1).build(
        MultiSourceDB(primary, secondary),
        SimpleNamespace(id="user-1"),
        [primary.id, secondary.id],
        "Bitcoin current price market data сейчас",
        freshness_max_age_seconds=3600,
        freshness_min_independent_hosts=1,
    )
    assert primary.final_url in verified
    assert secondary.final_url not in verified
    supplied = "\n".join(message.content for message in messages)
    assert primary.final_url in supplied
    assert secondary.final_url not in supplied


def test_supported_quality_requires_real_grounded_source_context():
    engine = AnswerQualityEngine()
    critic = {"ok": True, "issues": [], "summary": ""}
    ungrounded = engine.deterministic("323", [], set())
    grounded = engine.deterministic("323", [], {"https://example.com/source"})
    assert engine.final_status(ungrounded, critic) == "checked"
    assert engine.final_status(grounded, critic) == "supported"


def test_production_configuration_fails_closed_on_sqlite_and_default_sandbox_secret():
    settings = SimpleNamespace(
        env="production",
        database_url="sqlite+pysqlite:///./x1.db",
        admin_bootstrap_token="change-me",
        project_runtime_secret_key="change-me-runtime-secret",
        project_sandbox_backend="remote",
        project_sandbox_worker_token="change-me-sandbox-worker",
    )
    errors = set(_production_configuration_errors(settings))
    assert "production_database_must_not_be_sqlite" in errors
    assert "admin_bootstrap_token_is_default" in errors
    assert "project_runtime_secret_key_is_default" in errors
    assert "sandbox_worker_token_is_default" in errors


def test_sandbox_preview_operations_require_x1_owned_container(monkeypatch):
    monkeypatch.setattr(sandbox_worker, "_preview_metadata", lambda _ref: (False, 0))
    with pytest.raises(HTTPException) as exc:
        sandbox_worker._require_owned_preview("x1-preview-abcdef12")
    assert exc.value.status_code == 404


def test_sandbox_expired_preview_is_rejected_and_cleaned(monkeypatch):
    monkeypatch.setattr(sandbox_worker, "_preview_metadata", lambda _ref: (True, int(datetime.now().timestamp()) - 1))
    removed = []
    monkeypatch.setattr(sandbox_worker, "_run", lambda argv, timeout=10: removed.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(HTTPException) as exc:
        sandbox_worker._require_owned_preview("x1-preview-abcdef12")
    assert exc.value.status_code == 410
    assert removed and removed[0][:4] == ["docker", "rm", "-f", "x1-preview-abcdef12"]


def test_chat_releases_database_transaction_before_inference():
    text = (ROOT / "app" / "api" / "routes" / "chat.py").read_text("utf-8")
    commit_at = text.index("# Never hold a PostgreSQL connection")
    inference_at = text.index("async with request.app.state.user_governor.slot", commit_at)
    assert "db.commit()" in text[commit_at:inference_at]


def test_document_renderer_saturation_is_retryable_not_persistent_failure():
    text = (ROOT / "app" / "api" / "routes" / "documents.py").read_text("utf-8")
    busy = text.index("except DocumentBusyError")
    qa_error = text.index("except DocumentQAError", busy)
    block = text[busy:qa_error]
    assert "status_code=503" in block
    assert '"Retry-After": "3"' in block
    assert 'artifact.status = "qa_failed"' not in block


def test_production_images_and_python_bases_are_reproducibly_pinned():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "postgres:17.11-alpine3.24@sha256:" in compose
    assert "ghcr.io/searxng/searxng:" in compose and "@sha256:" in compose
    assert "ghcr.io/ggml-org/llama.cpp:server-" in compose and "@sha256:" in compose
    for name in ("Dockerfile", "Dockerfile.sandbox-runtime", "Dockerfile.sandbox-worker"):
        first = (ROOT / name).read_text("utf-8").splitlines()[0]
        assert first.startswith("FROM python:3.12.14-slim-bookworm@sha256:")


def test_optional_sidecars_do_not_prevent_core_app_startup():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    app_section = compose.split("\n  app:\n", 1)[1].split("\n  gate:\n", 1)[0]
    assert "db:\n        condition: service_healthy" in app_section
    assert "searxng:\n        condition: service_started" in app_section
    assert "sandbox-worker:\n        condition: service_started" in app_section


def test_installer_uses_runtime_neutral_searx_probe():
    installer = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    assert "docker compose exec -T searxng python -c" in installer
    assert "docker compose exec -T searxng sh -c \"wget" not in installer


def test_api_rate_limiter_uses_savepoint_and_bounded_old_windows():
    text = (ROOT / "app" / "services" / "api_access.py").read_text("utf-8")
    assert "with db.begin_nested()" in text
    assert "window_start - timedelta(minutes=2)" in text


def test_maintenance_service_is_wired_and_ephemeral_only():
    main = (ROOT / "app" / "main.py").read_text("utf-8")
    service = (ROOT / "app" / "services" / "maintenance.py").read_text("utf-8")
    assert "maintenance_loop(app)" in main
    for model in ("ApiRateLimitWindow", "SearchQueryCache", "AuthSession", "SystemHealthSnapshot", "BackgroundJob"):
        assert model in service
    for forbidden in ("ResourceExpenseEvent", "PaymentEvent", "Complaint", "Message", "ResearchSource"):
        assert f"delete({forbidden})" not in service


def test_runtime_release_gate_requires_component_acceptance_before_ai_journey():
    gate = (ROOT / "scripts" / "release_gate.py").read_text("utf-8")
    component = (ROOT / "scripts" / "component_acceptance.py").read_text("utf-8")
    assert 'run("component_acceptance"' in gate
    assert "scripts.component_acceptance" in gate
    assert gate.index('run("component_acceptance"') < gate.index('run("e2e_user_journey"')
    for marker in (
        '"register"',
        '"revoked_session_rejected"',
        '"memory_roundtrip"',
        '"file_integrity"',
        '"document_qa_passed"',
        '"document_released"',
        '"sandbox_isolated_available"',
        '"revoked_api_key_rejected"',
        '"account_export_contains_project"',
    ):
        assert marker in component


def test_sprint40_version_is_active():
    assert app.version == "0.40.0"
