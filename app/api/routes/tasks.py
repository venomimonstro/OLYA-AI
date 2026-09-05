from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Task, TaskCheckpoint, TaskCriterion, TaskEvidence, User
from app.schemas.tasks import (
    CheckpointCreate,
    CheckpointResponse,
    CriterionManualComplete,
    CriterionResponse,
    EvidenceCreate,
    EvidenceResponse,
    TaskCreate,
    TaskPatch,
    TaskResponse,
    TaskTransition,
)
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.tasks import (
    CompletionBlockedError,
    TaskConflictError,
    completion_blockers,
    create_checkpoint,
    require_task_access,
    require_task_mutation,
    task_criteria,
    transition_task,
    utcnow,
)

router = APIRouter(prefix="/v1", tags=["tasks"])


def _criterion_response(item: TaskCriterion) -> CriterionResponse:
    return CriterionResponse(
        id=item.id,
        text=item.text,
        required=item.required,
        verification_method=item.verification_method,
        satisfied=item.satisfied,
        satisfied_at=item.satisfied_at,
        verified_evidence_id=item.verified_evidence_id,
    )


def _task_response(db: Session, task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        created_by=task.created_by,
        title=task.title,
        goal=task.goal,
        constraints=task.constraints,
        status=task.status,
        current_step=task.current_step,
        state_version=task.state_version,
        completed_steps=task.completed_steps,
        max_steps=task.max_steps,
        compute_seconds_used=task.compute_seconds_used,
        max_compute_seconds=task.max_compute_seconds,
        criteria=[_criterion_response(item) for item in task_criteria(db, task.id)],
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _evidence_response(item: TaskEvidence) -> EvidenceResponse:
    return EvidenceResponse(
        id=item.id,
        task_id=item.task_id,
        criterion_id=item.criterion_id,
        kind=item.kind,
        source_ref=item.source_ref,
        summary=item.summary,
        state=item.state,
        created_by=item.created_by,
        verifier=item.verifier,
        created_at=item.created_at,
        verified_at=item.verified_at,
    )


def _checkpoint_response(item: TaskCheckpoint) -> CheckpointResponse:
    return CheckpointResponse(
        id=item.id,
        task_id=item.task_id,
        sequence=item.sequence,
        task_state_version=item.task_state_version,
        reason=item.reason,
        current_step=item.current_step,
        working_state=item.working_state,
        created_at=item.created_at,
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/projects/{project_id}/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    project_id: str,
    payload: TaskCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    require_project_role(db, user, project_id, "member")
    task = Task(
        project_id=project_id,
        created_by=user.id,
        title=payload.title,
        goal=payload.goal,
        constraints=payload.constraints,
        max_steps=payload.max_steps,
        max_compute_seconds=payload.max_compute_seconds,
    )
    db.add(task)
    db.flush()
    for ordinal, criterion in enumerate(payload.criteria):
        db.add(
            TaskCriterion(
                task_id=task.id,
                ordinal=ordinal,
                text=criterion.text.strip(),
                required=criterion.required,
                verification_method=criterion.verification_method,
            )
        )
    create_checkpoint(db, task, reason="created", current_step="", working_state={"status": "created"})
    db.commit()
    db.refresh(task)
    return _task_response(db, task)


@router.get("/projects/{project_id}/tasks", response_model=list[TaskResponse])
def list_tasks(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskResponse]:
    require_project_role(db, user, project_id, "viewer")
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.updated_at.desc())).all())
    return [_task_response(db, task) for task in tasks]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_access(db, user, task_id, "viewer")
    return _task_response(db, task)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def patch_task(
    task_id: str,
    payload: TaskPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    if task.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Terminal task is immutable")
    if task.state_version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task state version conflict")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        if key == "constraints" and value is not None:
            value = [item.strip() for item in value if item.strip()]
        setattr(task, key, value)
    task.updated_at = utcnow()
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/start", response_model=TaskResponse)
def start_task(task_id: str, payload: TaskTransition, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    try:
        transition_task(db, task, expected_version=payload.expected_version, target="running", current_step=payload.current_step, reason=payload.reason)
    except TaskConflictError as exc:
        raise _conflict(exc) from exc
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/pause", response_model=TaskResponse)
def pause_task(task_id: str, payload: TaskTransition, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    try:
        transition_task(db, task, expected_version=payload.expected_version, target="waiting", current_step=payload.current_step, reason=payload.reason or "paused")
    except TaskConflictError as exc:
        raise _conflict(exc) from exc
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/verify", response_model=TaskResponse)
def verify_task(task_id: str, payload: TaskTransition, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    try:
        transition_task(db, task, expected_version=payload.expected_version, target="verifying", current_step=payload.current_step, reason=payload.reason)
    except TaskConflictError as exc:
        raise _conflict(exc) from exc
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/complete", response_model=TaskResponse)
def complete_task(task_id: str, payload: TaskTransition, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    try:
        transition_task(db, task, expected_version=payload.expected_version, target="completed", current_step=payload.current_step, reason=payload.reason or "completed")
    except (TaskConflictError, CompletionBlockedError) as exc:
        raise _conflict(exc) from exc
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str, payload: TaskTransition, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    try:
        transition_task(db, task, expected_version=payload.expected_version, target="cancelled", current_step=payload.current_step, reason=payload.reason or "cancelled")
    except TaskConflictError as exc:
        raise _conflict(exc) from exc
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/criteria/{criterion_id}/manual-complete", response_model=TaskResponse)
def manual_complete_criterion(
    task_id: str,
    criterion_id: str,
    payload: CriterionManualComplete,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskResponse:
    task, _ = require_task_mutation(db, user, task_id)
    if task.state_version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task state version conflict")
    criterion = db.get(TaskCriterion, criterion_id)
    if criterion is None or criterion.task_id != task.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Criterion not found")
    if criterion.verification_method != "manual":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Criterion requires verified evidence")
    criterion.satisfied = True
    criterion.satisfied_at = utcnow()
    task.updated_at = utcnow()
    if payload.note.strip():
        db.add(TaskEvidence(task_id=task.id, criterion_id=criterion.id, kind="manual_attestation", summary=payload.note.strip(), state="submitted", created_by=user.id, verifier=""))
    db.commit()
    return _task_response(db, task)


@router.post("/tasks/{task_id}/evidence", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
def submit_evidence(
    task_id: str,
    payload: EvidenceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EvidenceResponse:
    task, _ = require_task_mutation(db, user, task_id)
    criterion: TaskCriterion | None = None
    if payload.criterion_id:
        criterion = db.get(TaskCriterion, payload.criterion_id)
        if criterion is None or criterion.task_id != task.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Criterion not found")
    evidence = TaskEvidence(
        task_id=task.id,
        criterion_id=criterion.id if criterion else None,
        kind=payload.kind,
        source_ref=payload.source_ref,
        summary=payload.summary,
        content_sha256=payload.content_sha256,
        state="submitted",
        created_by=user.id,
        verifier="",
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return _evidence_response(evidence)


@router.get("/tasks/{task_id}/evidence", response_model=list[EvidenceResponse])
def list_evidence(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[EvidenceResponse]:
    task, _ = require_task_access(db, user, task_id, "viewer")
    items = list(db.scalars(select(TaskEvidence).where(TaskEvidence.task_id == task.id).order_by(TaskEvidence.created_at)).all())
    return [_evidence_response(item) for item in items]


@router.post("/tasks/{task_id}/checkpoints", response_model=CheckpointResponse, status_code=status.HTTP_201_CREATED)
def add_checkpoint(
    task_id: str,
    payload: CheckpointCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckpointResponse:
    task, _ = require_task_mutation(db, user, task_id)
    if task.state_version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task state version conflict")
    if task.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Terminal task is immutable")
    if payload.current_step.strip():
        task.current_step = payload.current_step.strip()
    task.updated_at = utcnow()
    db.flush()
    checkpoint = create_checkpoint(db, task, reason=payload.reason, current_step=task.current_step, working_state=payload.working_state)
    db.commit()
    db.refresh(checkpoint)
    return _checkpoint_response(checkpoint)


@router.get("/tasks/{task_id}/checkpoints", response_model=list[CheckpointResponse])
def list_checkpoints(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CheckpointResponse]:
    task, _ = require_task_access(db, user, task_id, "viewer")
    items = list(db.scalars(select(TaskCheckpoint).where(TaskCheckpoint.task_id == task.id).order_by(TaskCheckpoint.sequence)).all())
    return [_checkpoint_response(item) for item in items]


@router.get("/tasks/{task_id}/completion-blockers", response_model=list[str])
def get_completion_blockers(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[str]:
    task, _ = require_task_access(db, user, task_id, "viewer")
    return completion_blockers(db, task)
