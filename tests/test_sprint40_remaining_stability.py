from __future__ import annotations

from pathlib import Path

from app.inference.router import choose_route
from app.services.quality import needs_fresh_grounding

ROOT = Path(__file__).resolve().parents[1]


def test_work_route_never_exceeds_running_deep_context_ceiling():
    decision = choose_route("обычный сложный вопрос", "work", normal_context=16384, deep_context=8192)
    assert decision.max_context_tokens == 8192
    deep = choose_route("аудит безопасности", "deep", normal_context=32768, deep_context=12288)
    assert deep.max_context_tokens == 12288


def test_inherently_current_queries_require_fresh_grounding():
    for query in (
        "курс доллара к рублю",
        "погода в Москве",
        "есть ли товар в наличии",
        "котировки акций",
        "bitcoin price in USD",
        "train schedule",
    ):
        assert needs_fresh_grounding(query), query


def test_installer_repairs_both_normal_and_deep_context_for_old_envs():
    text = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    assert "deep_context=bounded_int('X1_DEEP_CONTEXT_TOKENS'" in text
    assert "normal_context=bounded_int('X1_MAX_CONTEXT_TOKENS'" in text
    assert "setv('X1_DEEP_CONTEXT_TOKENS',deep_context)" in text
    assert "setv('X1_MAX_CONTEXT_TOKENS',min(normal_context,deep_context))" in text


def test_file_upload_enforces_disk_and_user_quota_and_releases_db_before_slow_io():
    text = (ROOT / "app" / "api" / "routes" / "files.py").read_text("utf-8")
    assert "file_storage_min_free_bytes" in text
    assert "file_storage_min_free_percent" in text
    assert "file_user_storage_quota_bytes" in text
    assert "FileParseBusyError" in text
    assert 'headers={"Retry-After": "3"}' in text

    body_read = text.index("content = await _read_limited_body")
    auth_commit = text.rfind("db.commit()", 0, body_read)
    assert auth_commit != -1

    parser = text.index("segments = await asyncio.to_thread")
    durable_processing = text.rfind("db.commit()", body_read, parser)
    assert durable_processing != -1
    assert "queue_timeout_seconds=float(settings.file_parse_queue_timeout_seconds)" in text


def test_restore_keeps_rollback_state_until_restored_app_is_verified():
    text = (ROOT / "scripts" / "restore.sh").read_text("utf-8")
    assert "rollback_cutover" in text
    assert "restored app could not become healthy and query PostgreSQL" in text
    verification = text.index("restored app could not become healthy and query PostgreSQL")
    expendable = text.index("# Only now is rollback state expendable.")
    assert verification < expendable
    assert text.index('dropdb -U x1 --if-exists --force "$OLD_DB"', expendable) > expendable
    assert text.index('rm -rf "$OLD_DATA"', expendable) > expendable


def test_restore_preserves_target_node_db_and_hardware_configuration():
    text = (ROOT / "scripts" / "restore.sh").read_text("utf-8")
    for key in (
        "POSTGRES_PASSWORD",
        "X1_DATABASE_URL",
        "X1_HOST_DATA_ROOT",
        "X1_LLAMA_MEMORY_LIMIT",
        "X1_LLAMA_THREADS",
        "X1_MAX_CONTEXT_TOKENS",
        "X1_DEEP_CONTEXT_TOKENS",
    ):
        assert f'"{key}"' in text


def test_sandbox_executions_have_expiry_and_are_reaped_after_worker_restart():
    text = (ROOT / "app" / "sandbox_worker_api.py").read_text("utf-8")
    assert "execution_expiry = int(time.time()) + int(payload.timeout_seconds)" in text
    assert '"--label", f"{_EXPIRY_LABEL}={execution_expiry}"' in text
    assert "def reap_expired_executions()" in text
    assert '"executions": reap_expired_executions()' in text
    assert "reap_expired_containers()" in text
    assert '"active_executions": executions' in text
