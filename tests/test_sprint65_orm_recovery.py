from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import Base, build_engine
from app.models import BackgroundJob, Conversation, Message, Project, Task, User, UserQuota


ROOT = Path(__file__).resolve().parents[1]


def test_registry_is_readable_and_has_no_runtime_payload_execution():
    registry = (ROOT / "app/models.py").read_text("utf-8")
    assert "models_core" in registry
    assert "models_migrations" in registry
    assert "exec(" not in registry
    assert "_models_impl.py.gz" not in registry
    assert not (ROOT / "app/_models_impl.py.gz").exists()


def test_migration_derived_models_are_reproducible():
    result = subprocess.run(
        [sys.executable, "scripts/generate_orm_models.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_complete_metadata_can_create_and_drop_clean_database():
    engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert len(Base.metadata.tables) >= 90
    assert all(table.primary_key.columns for table in Base.metadata.tables.values())
    Base.metadata.drop_all(engine)


def test_recovered_core_models_persist_with_application_defaults():
    engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="orm-recovery@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, name="Recovery")
        conversation = Conversation(owner_id=user.id, title="Recovered")
        db.add_all([project, conversation])
        db.flush()
        task = Task(project_id=project.id, created_by=user.id, title="Verify", goal="Persist")
        message = Message(conversation_id=conversation.id, role="user", content="hello")
        quota = UserQuota(user_id=user.id)
        job = BackgroundJob(kind="probe", user_id=user.id)
        db.add_all([task, message, quota, job])
        db.commit()

        assert task.state_version == 1
        assert quota.plan == "free"
        assert job.status == "queued"
        assert message.created_at is not None
