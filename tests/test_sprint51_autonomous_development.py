from pathlib import Path

import pytest

from app.models import AutonomousDevelopmentLedger
from app.services.autonomous_development import (
    AutonomousDevelopmentError,
    consume_subagent_slot,
    release_subagent_slot,
)


class FakeDB:
    def __init__(self):
        self.flushes = 0

    def flush(self):
        self.flushes += 1


def ledger(**overrides):
    values = dict(
        development_session_id="session-1",
        project_id="project-1",
        plan_id="plan-1",
        immutable_goal="Build the project",
        immutable_constraints={"constraints": {"cpu_only": True}},
        contract_sha256="a" * 64,
        status="active",
        revision=1,
        current_state={},
        decisions=[],
        completed_work=[],
        pending_work=[],
        failed_work=[],
        checkpoint_ref="",
        subagent_budget=8,
        max_parallel_subagents=1,
        subagent_calls_used=0,
        active_subagents=0,
    )
    values.update(overrides)
    return AutonomousDevelopmentLedger(**values)


def test_subagent_budget_is_hard_server_boundary():
    row = ledger(subagent_budget=1, subagent_calls_used=1)
    with pytest.raises(AutonomousDevelopmentError):
        consume_subagent_slot(FakeDB(), row)


def test_parallel_subagent_limit_is_hard_server_boundary():
    row = ledger(max_parallel_subagents=1, active_subagents=1)
    with pytest.raises(AutonomousDevelopmentError):
        consume_subagent_slot(FakeDB(), row)


def test_subagent_slot_updates_and_releases_persistent_counters():
    db = FakeDB(); row = ledger()
    consume_subagent_slot(db, row)
    assert row.subagent_calls_used == 1
    assert row.active_subagents == 1
    release_subagent_slot(db, row)
    assert row.active_subagents == 0
    assert db.flushes == 2


def test_resume_detects_stale_heartbeat_before_sync_refreshes_it():
    source = Path("app/services/autonomous_development.py").read_text(encoding="utf-8")
    previous = source.index("previous_heartbeat =")
    sync = source.index("ledger = sync_ledger", previous)
    stale = source.index("stale = bool(previous_heartbeat", previous)
    assert previous < stale < sync


def test_immutable_contract_is_not_overwritten_on_existing_ledger():
    source = Path("app/services/autonomous_development.py").read_text(encoding="utf-8")
    existing_branch = source.index("if ledger is not None:")
    return_pos = source.index("return ledger", existing_branch)
    constructor = source.index("ledger = AutonomousDevelopmentLedger(", existing_branch)
    assert existing_branch < return_pos < constructor
    assert "drift_detected" in source


def test_development_chat_persists_checkpoint_and_returns_ledger():
    route = Path("app/api/routes/development_chat.py").read_text(encoding="utf-8")
    schema = Path("app/schemas/development_chat.py").read_text(encoding="utf-8")
    assert "sync_ledger(" in route
    assert 'checkpoint_kind=str(action or command)[:32]' in route
    assert 'state["autonomous"] = serialize_ledger' in route
    assert "autonomous: dict | None = None" in schema


def test_engineering_roles_receive_server_owned_resume_state():
    source = Path("app/services/engineering.py").read_text(encoding="utf-8")
    assert "compact_plan_resume_context" in source
    assert '"autonomous_resume_ledger":autonomous' in source
    assert "trusted server-owned continuity state" in source
    assert "Never overwrite its immutable goal/constraints" in source


def test_maintenance_recovers_abandoned_subagent_leases():
    source = Path("app/services/maintenance.py").read_text(encoding="utf-8")
    assert "recover_abandoned_slots" in source
    assert 'counts["recovered_autonomous_leases"]' in source


def test_migration_extends_sprint45_head_and_has_versioned_checkpoints():
    migration = Path("alembic/versions/f51c0a11d9e2_add_autonomous_development_ledger.py").read_text(encoding="utf-8")
    assert 'revision = "f51c0a11d9e2"' in migration
    assert 'down_revision = "f45a10c2d8e1"' in migration
    assert "uq_autonomous_development_checkpoint_revision" in migration
    assert 'sa.Column("active_subagents"' in migration


def test_resume_context_is_bounded_and_chat_history_is_not_the_source_of_truth():
    source = Path("app/services/autonomous_development.py").read_text(encoding="utf-8")
    assert "def compact_resume_context" in source
    assert "max_chars: int = 18_000" in source
    assert '"schema": "x1.autonomous-development-state.v1"' in source
    assert "immutable_goal" in source
    assert "completed_work" in source
    assert "pending_work" in source
    assert "failed_work" in source
    assert "Message" not in source
