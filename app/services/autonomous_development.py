from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ArchitectureDecision,
    AutonomousDevelopmentCheckpoint,
    AutonomousDevelopmentLedger,
    DevelopmentChatSession,
    DevelopmentPlan,
    DevelopmentSprint,
    DevelopmentWorkItem,
    EngineeringExecution,
    EngineeringRun,
    utcnow,
)
from app.services.agent_contract import agent_completion_blockers, require_agent_progress_budget


class AutonomousDevelopmentError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _contract(plan: DevelopmentPlan) -> tuple[str, dict, str]:
    goal = str(plan.title or "").strip()
    constraints = {"constraints": plan.constraints or {}, "architecture": plan.architecture or {}}
    if not goal:
        raise AutonomousDevelopmentError("Development plan has no immutable goal/title")
    return goal, constraints, _sha({"goal": goal, "constraints": constraints})


def ensure_ledger(db: Session, session: DevelopmentChatSession, *, subagent_budget: int = 8, max_parallel_subagents: int = 1) -> AutonomousDevelopmentLedger:
    plan = db.get(DevelopmentPlan, session.plan_id)
    if plan is None:
        raise AutonomousDevelopmentError("Development plan not found")
    ledger = db.scalar(select(AutonomousDevelopmentLedger).where(AutonomousDevelopmentLedger.development_session_id == session.id))
    goal, constraints, contract_sha = _contract(plan)
    if ledger is not None:
        return ledger
    ledger = AutonomousDevelopmentLedger(
        development_session_id=session.id,
        project_id=session.project_id,
        plan_id=session.plan_id,
        immutable_goal=goal,
        immutable_constraints=constraints,
        contract_sha256=contract_sha,
        status="active",
        revision=1,
        current_state={},
        decisions=[],
        completed_work=[],
        pending_work=[],
        failed_work=[],
        checkpoint_ref="",
        subagent_budget=max(1, min(int(subagent_budget), 64)),
        max_parallel_subagents=max(1, min(int(max_parallel_subagents), 4)),
        subagent_calls_used=0,
        active_subagents=0,
        last_heartbeat_at=utcnow(),
    )
    db.add(ledger)
    db.flush()
    return ledger


def _work_item_row(item: DevelopmentWorkItem, sprint: DevelopmentSprint) -> dict[str, Any]:
    return {
        "sprint_id": sprint.id,
        "sprint_ordinal": sprint.ordinal,
        "sprint_title": sprint.title,
        "work_item_id": item.id,
        "ordinal": item.ordinal,
        "title": item.title,
        "kind": item.kind,
        "status": item.status,
        "task_id": item.task_id,
        "dependencies": list(item.dependencies or []),
    }


def _live_state(db: Session, ledger: AutonomousDevelopmentLedger) -> dict[str, Any]:
    plan = db.get(DevelopmentPlan, ledger.plan_id)
    if plan is None:
        raise AutonomousDevelopmentError("Development plan disappeared")
    goal, constraints, current_contract_sha = _contract(plan)
    sprints = list(db.scalars(select(DevelopmentSprint).where(DevelopmentSprint.plan_id == ledger.plan_id).order_by(DevelopmentSprint.ordinal)).all())
    sprint_ids = [row.id for row in sprints]
    items = list(db.scalars(select(DevelopmentWorkItem).where(DevelopmentWorkItem.sprint_id.in_(sprint_ids)).order_by(DevelopmentWorkItem.sprint_id, DevelopmentWorkItem.ordinal)).all()) if sprint_ids else []
    sprint_by_id = {row.id: row for row in sprints}

    completed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in items:
        sprint = sprint_by_id.get(item.sprint_id)
        if sprint is None:
            continue
        row = _work_item_row(item, sprint)
        if item.status == "completed":
            completed.append(row)
        elif item.status in {"blocked", "failed", "rolled_back"}:
            failed.append(row)
        else:
            pending.append(row)

    decisions = list(db.scalars(select(ArchitectureDecision).where(ArchitectureDecision.plan_id == ledger.plan_id, ArchitectureDecision.status == "active").order_by(ArchitectureDecision.created_at)).all())
    decision_rows = [
        {"id": row.id, "key": row.key, "decision": row.decision, "rationale": row.rationale, "status": row.status}
        for row in decisions[-100:]
    ]

    active_run = db.scalar(select(EngineeringRun).where(EngineeringRun.plan_id == ledger.plan_id, EngineeringRun.status.in_(["running", "approved", "blocked"])).order_by(EngineeringRun.updated_at.desc()))
    execution = None
    if active_run is not None:
        execution = db.scalar(select(EngineeringExecution).where(EngineeringExecution.engineering_run_id == active_run.id).order_by(EngineeringExecution.updated_at.desc()))

    return {
        "plan": {"id": plan.id, "status": plan.status, "current_sprint_ordinal": plan.current_sprint_ordinal},
        "contract": {
            "sha256": ledger.contract_sha256,
            "drift_detected": current_contract_sha != ledger.contract_sha256,
            "current_sha256": current_contract_sha,
            "current_goal_preview": goal[:500],
            "current_constraints_sha256": _sha(constraints),
        },
        "sprints": [{"id": row.id, "ordinal": row.ordinal, "title": row.title, "status": row.status} for row in sprints],
        "engineering": None if active_run is None else {
            "run_id": active_run.id,
            "work_item_id": active_run.work_item_id,
            "status": active_run.status,
            "current_role": active_run.current_role,
            "cycle": active_run.cycle,
            "state_version": active_run.state_version,
        },
        "execution": None if execution is None else {
            "execution_id": execution.id,
            "status": execution.status,
            "attempt": execution.attempt,
            "state_version": execution.state_version,
            "failure_reason": execution.failure_reason,
        },
        "counts": {"completed": len(completed), "pending": len(pending), "failed": len(failed)},
        "subagents": {
            "budget": ledger.subagent_budget,
            "calls_used": ledger.subagent_calls_used,
            "remaining": max(0, ledger.subagent_budget - ledger.subagent_calls_used),
            "active": ledger.active_subagents,
            "max_parallel": ledger.max_parallel_subagents,
        },
        "immutable_goal": ledger.immutable_goal,
        "immutable_constraints": ledger.immutable_constraints,
        "decisions": decision_rows,
        "completed_work": completed[-250:],
        "pending_work": pending[:250],
        "failed_work": failed[-250:],
    }


def _checkpoint_payload(ledger: AutonomousDevelopmentLedger, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "ledger_id": ledger.id,
        "development_session_id": ledger.development_session_id,
        "project_id": ledger.project_id,
        "plan_id": ledger.plan_id,
        "revision": ledger.revision,
        "immutable_goal": ledger.immutable_goal,
        "immutable_constraints": ledger.immutable_constraints,
        "contract_sha256": ledger.contract_sha256,
        "state": state,
    }


def sync_ledger(db: Session, session: DevelopmentChatSession, *, checkpoint_kind: str = "state", force_checkpoint: bool = False) -> AutonomousDevelopmentLedger:
    ledger = ensure_ledger(db, session)
    state = _live_state(db, ledger)
    blockers = agent_completion_blockers(db, ledger.plan_id)
    state["completion_gate"] = {
        "ready": not blockers,
        "blockers": blockers[:100],
        "proof": "canonical_tasks_verified_evidence_and_execution_state",
    }
    state_sha = _sha(state)
    previous_sha = _sha(ledger.current_state or {}) if ledger.current_state else ""
    ledger.current_state = state
    ledger.decisions = list(state["decisions"])
    ledger.completed_work = list(state["completed_work"])
    ledger.pending_work = list(state["pending_work"])
    ledger.failed_work = list(state["failed_work"])
    if state["contract"]["drift_detected"]:
        ledger.status = "blocked"
    elif state["plan"]["status"] == "completed":
        ledger.status = "completed" if not blockers else "blocked"
    else:
        ledger.status = "blocked" if state["failed_work"] and not state["pending_work"] else "active"
    ledger.last_heartbeat_at = utcnow()
    ledger.updated_at = utcnow()

    if force_checkpoint or state_sha != previous_sha or not ledger.checkpoint_ref:
        if ledger.checkpoint_ref:
            ledger.revision += 1
        checkpoint_row = AutonomousDevelopmentCheckpoint(
            ledger_id=ledger.id,
            revision=ledger.revision,
            kind=checkpoint_kind[:32],
            state_sha256=state_sha,
            payload=_checkpoint_payload(ledger, state),
        )
        db.add(checkpoint_row)
        db.flush()
        ledger.checkpoint_ref = checkpoint_row.id
    db.flush()
    return ledger


def latest_checkpoint(db: Session, ledger: AutonomousDevelopmentLedger) -> AutonomousDevelopmentCheckpoint | None:
    if ledger.checkpoint_ref:
        row = db.get(AutonomousDevelopmentCheckpoint, ledger.checkpoint_ref)
        if row is not None:
            return row
    return db.scalar(select(AutonomousDevelopmentCheckpoint).where(AutonomousDevelopmentCheckpoint.ledger_id == ledger.id).order_by(AutonomousDevelopmentCheckpoint.revision.desc()))


def _resume_dict(ledger: AutonomousDevelopmentLedger, checkpoint_row: AutonomousDevelopmentCheckpoint | None, *, heartbeat_was_stale: bool) -> dict[str, Any]:
    return {
        "schema": "x1.autonomous-development-state.v1",
        "ledger_id": ledger.id,
        "revision": ledger.revision,
        "checkpoint_id": checkpoint_row.id if checkpoint_row else "",
        "contract_sha256": ledger.contract_sha256,
        "immutable_goal": ledger.immutable_goal,
        "immutable_constraints": ledger.immutable_constraints,
        "status": ledger.status,
        "decisions": ledger.decisions,
        "completed": ledger.completed_work,
        "pending": ledger.pending_work,
        "failed": ledger.failed_work,
        "current_state": ledger.current_state,
        "subagent_budget": {
            "total": ledger.subagent_budget,
            "used": ledger.subagent_calls_used,
            "remaining": max(0, ledger.subagent_budget - ledger.subagent_calls_used),
            "active": ledger.active_subagents,
            "max_parallel": ledger.max_parallel_subagents,
        },
        "recovery": {"heartbeat_was_stale": heartbeat_was_stale, "resume_from_checkpoint": bool(checkpoint_row)},
    }


def resume_payload(db: Session, session: DevelopmentChatSession) -> dict[str, Any]:
    existing = db.scalar(select(AutonomousDevelopmentLedger).where(AutonomousDevelopmentLedger.development_session_id == session.id))
    previous_heartbeat = existing.last_heartbeat_at if existing is not None else None
    stale = bool(previous_heartbeat and previous_heartbeat < utcnow() - timedelta(minutes=10))
    ledger = sync_ledger(db, session, checkpoint_kind="resume")
    checkpoint_row = latest_checkpoint(db, ledger)
    payload = _resume_dict(ledger, checkpoint_row, heartbeat_was_stale=stale)
    payload["prompt_context"] = _canonical(payload)
    return payload


def compact_resume_context(db: Session, session: DevelopmentChatSession, *, max_chars: int = 18_000) -> str:
    payload = resume_payload(db, session)
    full = str(payload["prompt_context"])
    if len(full) <= max_chars:
        return full
    compact = {
        "schema": payload["schema"], "ledger_id": payload["ledger_id"], "revision": payload["revision"],
        "checkpoint_id": payload["checkpoint_id"], "contract_sha256": payload["contract_sha256"],
        "immutable_goal": payload["immutable_goal"], "immutable_constraints": payload["immutable_constraints"],
        "status": payload["status"], "decisions": payload["decisions"][-30:], "completed": payload["completed"][-50:],
        "pending": payload["pending"][:80], "failed": payload["failed"][-30:], "subagent_budget": payload["subagent_budget"],
        "recovery": payload["recovery"], "completion_gate": payload["current_state"].get("completion_gate", {}),
    }
    return _canonical(compact)[:max_chars]


def compact_plan_resume_context(db: Session, plan_id: str, *, max_chars: int = 18_000) -> str:
    ledger = db.scalar(select(AutonomousDevelopmentLedger).where(AutonomousDevelopmentLedger.plan_id == plan_id).order_by(AutonomousDevelopmentLedger.updated_at.desc()))
    if ledger is None:
        return ""
    checkpoint_row = latest_checkpoint(db, ledger)
    payload = _resume_dict(ledger, checkpoint_row, heartbeat_was_stale=False)
    compact = {
        "schema": payload["schema"], "ledger_id": payload["ledger_id"], "revision": payload["revision"],
        "checkpoint_id": payload["checkpoint_id"], "contract_sha256": payload["contract_sha256"],
        "immutable_goal": payload["immutable_goal"], "immutable_constraints": payload["immutable_constraints"],
        "status": payload["status"], "decisions": payload["decisions"][-30:], "completed": payload["completed"][-50:],
        "pending": payload["pending"][:80], "failed": payload["failed"][-30:], "subagent_budget": payload["subagent_budget"],
        "completion_gate": payload["current_state"].get("completion_gate", {}),
    }
    return _canonical(compact)[:max_chars]


def consume_subagent_slot(db: Session, ledger: AutonomousDevelopmentLedger) -> None:
    try:
        require_agent_progress_budget("autonomous subagent admission")
    except Exception as exc:
        raise AutonomousDevelopmentError(str(exc)) from exc
    if ledger.status in {"blocked", "completed"}:
        raise AutonomousDevelopmentError(f"Autonomous ledger cannot advance from {ledger.status}")
    if ledger.subagent_calls_used >= ledger.subagent_budget:
        raise AutonomousDevelopmentError("Autonomous subagent budget exhausted")
    if ledger.active_subagents >= ledger.max_parallel_subagents:
        raise AutonomousDevelopmentError("Autonomous subagent parallelism limit reached")
    ledger.subagent_calls_used += 1
    ledger.active_subagents += 1
    ledger.last_heartbeat_at = utcnow()
    ledger.updated_at = utcnow()
    db.flush()


def release_subagent_slot(db: Session, ledger: AutonomousDevelopmentLedger) -> None:
    ledger.active_subagents = max(0, int(ledger.active_subagents) - 1)
    ledger.last_heartbeat_at = utcnow()
    ledger.updated_at = utcnow()
    db.flush()


@contextmanager
def subagent_slot(db: Session, ledger: AutonomousDevelopmentLedger) -> Iterator[None]:
    consume_subagent_slot(db, ledger)
    try:
        yield
    finally:
        release_subagent_slot(db, ledger)


def recover_abandoned_slots(db: Session, *, stale_minutes: int = 10) -> int:
    cutoff = utcnow() - timedelta(minutes=max(1, int(stale_minutes)))
    rows = list(db.scalars(select(AutonomousDevelopmentLedger).where(AutonomousDevelopmentLedger.active_subagents > 0, AutonomousDevelopmentLedger.last_heartbeat_at < cutoff)).all())
    for ledger in rows:
        ledger.active_subagents = 0
        ledger.last_heartbeat_at = utcnow()
        ledger.updated_at = utcnow()
    if rows:
        db.flush()
    return len(rows)


def serialize_ledger(db: Session, session: DevelopmentChatSession) -> dict[str, Any]:
    payload = resume_payload(db, session)
    payload.pop("prompt_context", None)
    return payload
