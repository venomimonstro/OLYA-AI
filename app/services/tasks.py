from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Task, TaskCheckpoint, TaskCriterion, TaskEvidence, User
from app.services.access import ROLE_RANK, project_role, require_project_role


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MUTABLE_STATUSES = {"created", "running", "waiting", "verifying"}


class TaskConflictError(RuntimeError):
    pass


class CompletionBlockedError(RuntimeError):
    pass


class TaskBudgetExceededError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_version(task: Task, expected_version: int) -> None:
    if task.state_version != expected_version:
        raise TaskConflictError(f"Task state changed: expected {expected_version}, current {task.state_version}")


def require_task_access(db: Session, user: User, task_id: str, minimum_project_role: str = "viewer") -> tuple[Task, str]:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    project, role = require_project_role(db, user, task.project_id, minimum_project_role)
    _ = project
    return task, role


def require_task_mutation(db: Session, user: User, task_id: str) -> tuple[Task, str]:
    task, role = require_task_access(db, user, task_id, "member")
    if task.created_by != user.id and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task mutation not allowed")
    return task, role


def task_criteria(db: Session, task_id: str) -> list[TaskCriterion]:
    return list(db.scalars(select(TaskCriterion).where(TaskCriterion.task_id == task_id).order_by(TaskCriterion.ordinal)).all())


def completion_blockers(db: Session, task: Task) -> list[str]:
    blockers: list[str] = []
    for criterion in task_criteria(db, task.id):
        if not criterion.required:
            continue
        if not criterion.satisfied:
            blockers.append(f"criterion:{criterion.id}:not_satisfied")
            continue
        if criterion.verification_method == "evidence":
            if not criterion.verified_evidence_id:
                blockers.append(f"criterion:{criterion.id}:missing_verified_evidence")
                continue
            evidence = db.get(TaskEvidence, criterion.verified_evidence_id)
            if evidence is None or evidence.state != "verified" or evidence.task_id != task.id:
                blockers.append(f"criterion:{criterion.id}:evidence_not_verified")
    if task.completed_steps > task.max_steps:
        blockers.append("max_steps_exceeded")
    if task.compute_seconds_used > task.max_compute_seconds:
        blockers.append("compute_budget_exceeded")
    return blockers


def create_checkpoint(db: Session, task: Task, *, reason: str, current_step: str, working_state: dict[str, Any] | None = None) -> TaskCheckpoint:
    latest = db.scalar(select(func.max(TaskCheckpoint.sequence)).where(TaskCheckpoint.task_id == task.id)) or 0
    checkpoint = TaskCheckpoint(task_id=task.id, sequence=latest + 1, task_state_version=task.state_version, reason=reason, current_step=current_step, working_state=working_state or {})
    db.add(checkpoint)
    db.flush()
    return checkpoint


def transition_task(db: Session, task: Task, *, expected_version: int, target: str, current_step: str | None = None, reason: str = "") -> Task:
    _require_version(task, expected_version)
    if task.status in TERMINAL_STATUSES:
        raise TaskConflictError(f"Terminal task cannot transition from {task.status}")
    allowed: dict[str, set[str]] = {"created": {"running", "cancelled"}, "running": {"waiting", "verifying", "failed", "cancelled"}, "waiting": {"running", "failed", "cancelled"}, "verifying": {"running", "completed", "failed", "cancelled"}}
    if target not in allowed.get(task.status, set()):
        raise TaskConflictError(f"Invalid task transition {task.status} -> {target}")
    if target == "completed":
        blockers = completion_blockers(db, task)
        if blockers:
            raise CompletionBlockedError(",".join(blockers))
        task.completed_at = utcnow()
    if current_step is not None:
        task.current_step = current_step.strip()
    task.status = target
    task.updated_at = utcnow()
    if target in {"waiting", "failed", "cancelled", "completed"}:
        db.flush()
        create_checkpoint(db, task, reason=reason or target, current_step=task.current_step, working_state={"status": task.status})
    return task


def ensure_task_compute_available(task: Task, estimated_compute_seconds: int) -> None:
    if estimated_compute_seconds < 0:
        raise ValueError("estimated_compute_seconds must be non-negative")
    if task.compute_seconds_used + estimated_compute_seconds > task.max_compute_seconds:
        raise TaskBudgetExceededError("Task local compute budget exhausted")


def record_task_compute(db: Session, task_id: str, actual_compute_seconds: int) -> None:
    if actual_compute_seconds <= 0:
        return
    db.execute(update(Task).execution_options(synchronize_session=False).where(Task.id == task_id).values(compute_seconds_used=Task.compute_seconds_used + actual_compute_seconds))
    db.flush()


def reserve_runtime_budget(db: Session, task: Task, *, expected_version: int, estimated_compute_seconds: int, step_count: int = 1, current_step: str = "") -> bool:
    _require_version(task, expected_version)
    if task.status not in {"running", "verifying"}:
        raise TaskConflictError(f"Cannot reserve runtime budget while task is {task.status}")
    if estimated_compute_seconds < 0 or step_count < 0:
        raise ValueError("Runtime budget deltas must be non-negative")
    next_steps = task.completed_steps + step_count
    next_compute = task.compute_seconds_used + estimated_compute_seconds
    if next_steps > task.max_steps or next_compute > task.max_compute_seconds:
        task.status = "waiting"
        if current_step.strip():
            task.current_step = current_step.strip()
            task.updated_at = utcnow()
        db.flush()
        create_checkpoint(db, task, reason="budget_exhausted", current_step=task.current_step, working_state={"status": "waiting", "completed_steps": task.completed_steps, "compute_seconds_used": task.compute_seconds_used, "max_steps": task.max_steps, "max_compute_seconds": task.max_compute_seconds})
        return False
    task.completed_steps = next_steps
    task.compute_seconds_used = next_compute
    if current_step.strip():
        task.current_step = current_step.strip()
    task.updated_at = utcnow()
    return True


def add_verified_evidence(db: Session, *, task: Task, criterion: TaskCriterion | None, kind: str, source_ref: str, summary: str, verifier: str, content_sha256: str | None = None) -> TaskEvidence:
    evidence = TaskEvidence(task_id=task.id, criterion_id=criterion.id if criterion else None, kind=kind, source_ref=source_ref, summary=summary, content_sha256=content_sha256, state="verified", verifier=verifier, verified_at=utcnow())
    db.add(evidence)
    db.flush()
    if criterion is not None:
        criterion.satisfied = True
        criterion.satisfied_at = utcnow()
        criterion.verified_evidence_id = evidence.id
    task.updated_at = utcnow()
    return evidence
