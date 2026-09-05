from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DevelopmentCheckpoint, DevelopmentPlan, DevelopmentSprint, UsageEvent, User, utcnow
from app.schemas.development import (
    ArchitectDraftRequest,
    ArchitectDraftResponse,
    CheckpointCreate,
    DecisionCreate,
    DevelopmentCheckpointRead,
    DevelopmentPlanCreate,
    DevelopmentPlanPatch,
    FutureReplanRequest,
    DevelopmentPlanRead,
    SprintActivate,
)
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.inference.client import LlamaUnavailable
from app.services.quota import QuotaExceededError, ensure_compute_available
from app.services.resource_governor import ResourceBusyError
from app.services.user_resource_governor import UserConcurrencyBusyError
from app.services.development import (
    DevelopmentError,
    activate_sprint,
    architect_messages,
    add_decision,
    complete_sprint,
    create_checkpoint,
    extract_json_object,
    create_plan,
    normalize_requirements,
    refresh_sprint_state,
    replan_future_sprints,
    serialize_plan,
    validate_runtime_binding,
)

router = APIRouter(prefix="/v1/development-plans", tags=["development-plans"])


def _plan_access(db: Session, user: User, plan_id: str, minimum: str = "viewer") -> DevelopmentPlan:
    plan = db.get(DevelopmentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Development plan not found")
    require_project_role(db, user, plan.project_id, minimum)
    return plan


def _expect_version(plan: DevelopmentPlan, expected: int) -> None:
    if plan.state_version != expected:
        raise HTTPException(status_code=409, detail="Development plan state version conflict")


@router.post("/architect-draft", response_model=ArchitectDraftResponse, status_code=status.HTTP_201_CREATED)
async def architect_draft(payload: ArchitectDraftRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_project_role(db, user, payload.project_id, "manager")
    if db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == payload.project_id)) is not None:
        raise HTTPException(status_code=409, detail="Development plan already exists")
    try:
        validate_runtime_binding(db, payload.project_id, payload.runtime_id)
        quota = ensure_compute_available(db, user, request.app.state.settings, reserve_seconds=180)
    except DevelopmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QuotaExceededError as exc:
        db.rollback(); raise HTTPException(status_code=429, detail=str(exc)) from exc
    started = perf_counter()
    try:
        async with request.app.state.user_governor.slot(user.id, quota.max_concurrent_inference):
            async with request.app.state.governor.slot():
                raw = await request.app.state.llama.chat(
                    architect_messages(payload.product_brief, payload.constraints, payload.target_sprints),
                    max_tokens=5000, reasoning=True,
                )
    except UserConcurrencyBusyError as exc:
        db.rollback(); raise HTTPException(status_code=429, detail="Account inference limit reached") from exc
    except ResourceBusyError as exc:
        db.rollback(); raise HTTPException(status_code=503, detail="Local inference queue is full") from exc
    except LlamaUnavailable as exc:
        db.rollback(); raise HTTPException(status_code=503, detail="Local architect inference unavailable") from exc
    inference_ms = int((perf_counter() - started) * 1000)
    try:
        draft = extract_json_object(raw)
        create_payload = DevelopmentPlanCreate.model_validate({
            "project_id": payload.project_id,
            "runtime_id": payload.runtime_id,
            "title": draft.get("title") or "Development Plan",
            "product_brief": payload.product_brief,
            "requirements": draft.get("requirements") or [],
            "architecture": draft.get("architecture") or {},
            "constraints": payload.constraints,
            "sprints": draft.get("sprints") or [],
        })
        plan = create_plan(db, user_id=user.id, payload=create_payload)
    except Exception as exc:
        db.rollback()
        detail = str(exc) if isinstance(exc, DevelopmentError) else "Architect output failed plan validation"
        raise HTTPException(status_code=502, detail=detail) from exc
    db.add(UsageEvent(
        user_id=user.id, project_id=payload.project_id, conversation_id=None, mode="deep",
        raw_chars=len(payload.product_brief), compiled_chars=len(payload.product_brief), output_chars=len(raw),
        duration_ms=inference_ms, inference_ms=inference_ms, queue_ms=0, success=True,
    ))
    db.commit(); db.refresh(plan)
    return ArchitectDraftResponse(plan=serialize_plan(db, plan), model=request.app.state.settings.llama_model_name, inference_ms=inference_ms)


@router.post("", response_model=DevelopmentPlanRead, status_code=status.HTTP_201_CREATED)
def post_plan(payload: DevelopmentPlanCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_project_role(db, user, payload.project_id, "manager")
    try:
        plan = create_plan(db, user_id=user.id, payload=payload)
        db.commit(); db.refresh(plan)
        return serialize_plan(db, plan)
    except DevelopmentError as exc:
        db.rollback()
        code = 409 if "already exists" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/project/{project_id}", response_model=DevelopmentPlanRead)
def get_plan_for_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_project_role(db, user, project_id, "viewer")
    plan = db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == project_id))
    if plan is None:
        raise HTTPException(status_code=404, detail="Development plan not found")
    return serialize_plan(db, plan)


@router.get("/{plan_id}", response_model=DevelopmentPlanRead)
def get_plan(plan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_plan(db, _plan_access(db, user, plan_id))


@router.patch("/{plan_id}", response_model=DevelopmentPlanRead)
def patch_plan(plan_id: str, payload: DevelopmentPlanPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "manager")
    _expect_version(plan, payload.expected_version)
    if payload.product_brief is not None: plan.product_brief = payload.product_brief.strip()
    if payload.requirements is not None: plan.requirements = normalize_requirements(payload.requirements)
    if payload.architecture is not None: plan.architecture = payload.architecture
    if payload.constraints is not None: plan.constraints = [x.strip() for x in payload.constraints if x.strip()]
    if payload.status is not None: plan.status = payload.status
    plan.updated_at = utcnow()
    db.commit(); db.refresh(plan)
    return serialize_plan(db, plan)


@router.post("/{plan_id}/replan", response_model=DevelopmentPlanRead)
def post_replan(plan_id: str, payload: FutureReplanRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "manager")
    _expect_version(plan, payload.expected_version)
    try:
        replan_future_sprints(db, plan, payload.future_sprints, user_id=user.id, reason=payload.reason)
        db.commit(); db.refresh(plan)
        return serialize_plan(db, plan)
    except DevelopmentError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{plan_id}/decisions", response_model=DevelopmentPlanRead, status_code=status.HTTP_201_CREATED)
def post_decision(plan_id: str, payload: DecisionCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "manager")
    _expect_version(plan, payload.expected_version)
    try:
        add_decision(db, plan, user_id=user.id, key=payload.key, title=payload.title, decision=payload.decision, rationale=payload.rationale)
        db.commit(); db.refresh(plan)
        return serialize_plan(db, plan)
    except DevelopmentError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{plan_id}/sprints/{ordinal}/activate", response_model=DevelopmentPlanRead)
def post_activate(plan_id: str, ordinal: int, payload: SprintActivate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "member")
    _expect_version(plan, payload.expected_version)
    try:
        activate_sprint(db, plan, ordinal, user.id)
        db.commit(); db.refresh(plan)
        return serialize_plan(db, plan)
    except DevelopmentError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{plan_id}/sprints/{ordinal}/refresh", response_model=DevelopmentPlanRead)
def post_refresh(plan_id: str, ordinal: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "member")
    sprint = db.scalar(select(DevelopmentSprint).where(DevelopmentSprint.plan_id == plan.id, DevelopmentSprint.ordinal == ordinal))
    if sprint is None: raise HTTPException(status_code=404, detail="Sprint not found")
    refresh_sprint_state(db, plan, sprint)
    db.commit(); db.refresh(plan)
    return serialize_plan(db, plan)


@router.post("/{plan_id}/sprints/{ordinal}/complete", response_model=DevelopmentPlanRead)
def post_complete(plan_id: str, ordinal: int, payload: SprintActivate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "member")
    _expect_version(plan, payload.expected_version)
    try:
        complete_sprint(db, plan, ordinal)
        db.commit(); db.refresh(plan)
        return serialize_plan(db, plan)
    except DevelopmentError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{plan_id}/checkpoints", response_model=DevelopmentCheckpointRead, status_code=status.HTTP_201_CREATED)
def post_checkpoint(plan_id: str, payload: CheckpointCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "member")
    _expect_version(plan, payload.expected_version)
    try:
        row = create_checkpoint(db, plan, user_id=user.id, reason=payload.reason, runtime_snapshot_id=payload.runtime_snapshot_id)
        db.commit(); db.refresh(row)
        return row
    except DevelopmentError as exc:
        db.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{plan_id}/checkpoints", response_model=list[DevelopmentCheckpointRead])
def get_checkpoints(plan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = _plan_access(db, user, plan_id, "viewer")
    return list(db.scalars(select(DevelopmentCheckpoint).where(
        DevelopmentCheckpoint.plan_id == plan.id
    ).order_by(DevelopmentCheckpoint.sequence.desc())).all())
