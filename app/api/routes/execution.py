from __future__ import annotations

import hashlib
import json
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.inference.client import LlamaUnavailable
from app.models import EngineeringExecution, EngineeringRun, ProjectRuntime, Task, TaskEvidence, UsageEvent, User, utcnow
from app.schemas.engineering import EngineeringExecutionRead, ExecutionCreate, ExecuteApprovedRequest
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.engineering_execution import (
    ExecutionError, add_event, apply_patch, build_patch_messages, can_run_unsafe, create_execution,
    ensure_snapshot, parse_patch, rollback_changed_files, run_verification, serialize_execution, verification_commands,
    _approved_scope,
)
from app.services.quota import QuotaExceededError, ensure_compute_available
from app.services.resource_governor import ResourceBusyError
from app.services.tasks import TaskBudgetExceededError, add_verified_evidence, ensure_task_compute_available, record_task_compute
from app.services.user_resource_governor import UserConcurrencyBusyError

router = APIRouter(prefix="/v1/engineering-executions", tags=["engineering-execution"])


def _access(db: Session, user: User, execution_id: str, minimum: str = "viewer") -> EngineeringExecution:
    row = db.get(EngineeringExecution, execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Engineering execution not found")
    require_project_role(db, user, row.project_id, minimum)
    return row


@router.post("", response_model=EngineeringExecutionRead, status_code=status.HTTP_201_CREATED)
def create_row(payload: ExecutionCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.get(EngineeringRun, payload.engineering_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Engineering run not found")
    require_project_role(db, user, run.project_id, "member")
    try:
        row = create_execution(db, user_id=user.id, run=run, max_repairs=payload.max_repairs)
        db.commit(); db.refresh(row)
        return serialize_execution(db, row)
    except ExecutionError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{execution_id}", response_model=EngineeringExecutionRead)
def get_row(execution_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_execution(db, _access(db, user, execution_id))


@router.post("/{execution_id}/execute", response_model=EngineeringExecutionRead)
async def execute(execution_id: str, payload: ExecuteApprovedRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _access(db, user, execution_id, "member")
    if row.state_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Engineering execution state version conflict")
    if row.status in {"verified", "rolled_back", "blocked"}:
        raise HTTPException(status_code=409, detail=f"Execution cannot run from {row.status}")
    run = db.get(EngineeringRun, row.engineering_run_id)
    runtime = db.get(ProjectRuntime, row.runtime_id)
    task = db.get(Task, row.task_id)
    if run is None or runtime is None or task is None:
        raise HTTPException(status_code=409, detail="Execution dependencies are missing")
    try:
        ensure_snapshot(db, row, user.id)
        row.status = "running"; row.started_at = row.started_at or utcnow(); row.failure_reason = ""
        db.commit(); db.refresh(row)
    except ExecutionError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc

    while row.attempt <= row.max_repairs:
        db.refresh(task)
        row.attempt += 1
        db.commit(); db.refresh(row)
        try:
            ensure_task_compute_available(task, 120)
            quota = ensure_compute_available(db, user, request.app.state.settings, reserve_seconds=120)
            messages, input_sha = build_patch_messages(db, row)
            scope, approved_files, _ = _approved_scope(db, run)
        except (TaskBudgetExceededError, QuotaExceededError) as exc:
            row.status = "blocked"; row.failure_reason = str(exc); add_event(db, row, "budget", "blocked", {"detail": str(exc)})
            db.commit(); raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ExecutionError as exc:
            row.status = "blocked"; row.failure_reason = str(exc); add_event(db, row, "prepare", "failed", {"detail": str(exc)})
            db.commit(); raise HTTPException(status_code=409, detail=str(exc)) from exc

        started = perf_counter()
        try:
            async with request.app.state.user_governor.slot(user.id, quota.max_concurrent_inference):
                async with request.app.state.governor.slot():
                    raw = await request.app.state.llama.chat(messages, max_tokens=5000, reasoning=True)
        except UserConcurrencyBusyError as exc:
            raise HTTPException(status_code=429, detail="Account inference limit reached") from exc
        except ResourceBusyError as exc:
            raise HTTPException(status_code=503, detail="Local inference queue is full") from exc
        except LlamaUnavailable as exc:
            raise HTTPException(status_code=503, detail="Local implementation inference unavailable") from exc
        inference_ms = int((perf_counter() - started) * 1000)
        usage = dict(user_id=user.id, project_id=row.project_id, conversation_id=None, mode="deep",
                     raw_chars=sum(len(x.content) for x in messages), compiled_chars=sum(len(x.content) for x in messages),
                     output_chars=len(raw), duration_ms=inference_ms, inference_ms=inference_ms, queue_ms=0)
        record_task_compute(db, row.task_id, max(1, (inference_ms + 999)//1000))
        try:
            patch = parse_patch(raw, approved_files=approved_files, scope=scope)
            db.add(UsageEvent(**usage, success=True))
            add_event(db, row, "implementation", "ok", {"attempt": row.attempt, "input_sha256": input_sha, "summary": patch["summary"]})
            apply_patch(db, row, patch)
            db.commit(); db.refresh(row)
        except ExecutionError as exc:
            db.add(UsageEvent(**usage, success=False)); row.status = "blocked"; row.failure_reason = str(exc)
            add_event(db, row, "implementation", "failed", {"attempt": row.attempt, "detail": str(exc)})
            db.commit(); raise HTTPException(status_code=502, detail=str(exc)) from exc

        commands = verification_commands(db, row, patch)
        # Always add deterministic syntax checks for changed Python files when not already present.
        for change in row.change_manifest or []:
            path = change["path"]
            if path.endswith(".py"):
                cmd = ["python", "-m", "py_compile", path]
                if cmd not in commands:
                    commands.append(cmd)
        unsafe = can_run_unsafe(runtime, request.app.state.settings.code_allow_unsafe_commands)
        ok, results = run_verification(db, row, commands, timeout_seconds=request.app.state.settings.project_sandbox_command_timeout_seconds, allow_unsafe=unsafe, sandbox_settings=request.app.state.settings)
        if ok and commands:
            digest = hashlib.sha256(json.dumps(results, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            add_verified_evidence(db, task=task, criterion=None, kind="engineering_verification",
                                  source_ref=f"engineering-execution:{row.id}",
                                  summary=f"Execution verification passed: {len(results)} command(s)",
                                  verifier="x1.engineering-execution", content_sha256=digest)
            row.status = "verified"; row.completed_at = utcnow(); row.failure_reason = ""
            add_event(db, row, "completed", "verified", {"commands": len(results), "evidence_sha256": digest, "preview": "not_configured"})
            db.commit(); db.refresh(row)
            return serialize_execution(db, row)

        reason = "No executable verification commands" if not commands else "Verification failed"
        row.failure_reason = reason
        try:
            rollback_changed_files(db, row)
            row.status = "needs_repair" if row.attempt <= row.max_repairs else "rolled_back"
            add_event(db, row, "attempt_result", row.status, {"attempt": row.attempt, "reason": reason})
            db.commit(); db.refresh(row)
        except ExecutionError as exc:
            row.status = "blocked"; row.failure_reason = f"Rollback failed: {exc}"
            add_event(db, row, "rollback", "failed", {"detail": str(exc)})
            db.commit(); raise HTTPException(status_code=500, detail=row.failure_reason) from exc
        if row.status == "rolled_back":
            return serialize_execution(db, row)

    return serialize_execution(db, row)


@router.post("/{execution_id}/rollback", response_model=EngineeringExecutionRead)
def rollback(execution_id: str, payload: ExecuteApprovedRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _access(db, user, execution_id, "member")
    if row.state_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Engineering execution state version conflict")
    if not row.change_manifest:
        raise HTTPException(status_code=409, detail="Execution has no applied changes")
    try:
        rollback_changed_files(db, row)
        row.status = "rolled_back"; row.completed_at = utcnow(); row.failure_reason = "Manual rollback"
        db.commit(); db.refresh(row)
        return serialize_execution(db, row)
    except ExecutionError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc
