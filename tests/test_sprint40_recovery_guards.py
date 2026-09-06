from pathlib import Path

from app.db import _connect_args

ROOT = Path(__file__).resolve().parents[1]


def test_postgres_waits_are_bounded(monkeypatch):
    monkeypatch.setenv("X1_DATABASE_CONNECT_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("X1_DATABASE_STATEMENT_TIMEOUT_MS", "31000")
    monkeypatch.setenv("X1_DATABASE_LOCK_TIMEOUT_MS", "9000")
    monkeypatch.setenv("X1_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS", "61000")
    args = _connect_args("postgresql+psycopg://x1:secret@db:5432/x1")
    assert args["connect_timeout"] == 7
    assert "statement_timeout=31000" in args["options"]
    assert "lock_timeout=9000" in args["options"]
    assert "idle_in_transaction_session_timeout=61000" in args["options"]


def test_document_and_job_writes_are_serialized():
    documents = (ROOT / "app/api/routes/documents.py").read_text("utf-8")
    jobs = (ROOT / "app/services/jobs.py").read_text("utf-8")
    assert ".with_for_update()" in documents
    assert ".where(User.id == candidate.user_id)" in jobs
    assert ".with_for_update()" in jobs
    assert "BackgroundJob.lease_expires_at > now" in jobs


def test_backup_restore_and_update_stop_detached_sandbox_writers():
    for name in ("backup.sh", "restore.sh", "update.sh"):
        text = (ROOT / "scripts" / name).read_text("utf-8")
        assert "x1.sandbox.preview=true" in text
        assert "x1.sandbox.execution=true" in text
    backup = (ROOT / "scripts/backup.sh").read_text("utf-8")
    assert "docker compose stop app image-worker sandbox-worker" in backup
    assert "format=x1-backup-v4" in backup


def test_restore_uses_real_free_disk_not_fixed_20gb_ceiling():
    restore = (ROOT / "scripts/restore.sh").read_text("utf-8")
    drill = (ROOT / "scripts/restore_drill.sh").read_text("utf-8")
    for text in (restore, drill):
        assert "shutil.disk_usage(destination).free" in text
        assert "X1_RESTORE_MAX_DATA_BYTES" in text


def test_image_worker_survives_lost_job_lease():
    text = (ROOT / "scripts/image_worker.py").read_text("utf-8")
    assert "isinstance(exc, JobLeaseLostError)" in text
    assert "heartbeat_lost.is_set()" in text
    assert "failure_db.rollback()" in text
