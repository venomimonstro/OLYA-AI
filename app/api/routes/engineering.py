from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.inference.client import LlamaUnavailable
from app.models import DevelopmentWorkItem, EngineeringRun, Task, UsageEvent, User
from app.schemas.engineering import EngineeringRunCreate, EngineeringRunRead, ExecuteRoleRequest
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.engineering import EngineeringError, build_role_messages, create_engineering_run, parse_role_output, persist_role_result, serialize_run
from app.services.quota import QuotaExceededError, ensure_compute_available
from app.services.resource_governor import ResourceBusyError
from app.services.tasks import TaskBudgetExceededError, ensure_task_compute_available, record_task_compute
from app.services.user_resource_governor import UserConcurrencyBusyError

router = APIRouter(prefix="/v1/engineering-runs", tags=["engineering"])


def _access(db: Session, user: User, run_id: str, minimum: str = "viewer") -> EngineeringRun:
    run = db.get(EngineeringRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Engineering run not found")
    require_project_role(db, user, run.project_id, minimum)
    return run

@router.post("", response_model=EngineeringRunRead, status_code=status.HTTP_201_CREATED)
def create_run(payload: EngineeringRunCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(DevelopmentWorkItem, payload.work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found")
    from app.models import DevelopmentSprint, DevelopmentPlan
    sprint = db.get(DevelopmentSprint, item.sprint_id)
    plan = db.get(DevelopmentPlan, sprint.plan_id) if sprint else None
    if plan is None:
        raise HTTPException(status_code=404, detail="Development plan not found")
    require_project_role(db, user, plan.project_id, "member")
    try:
        run = create_engineering_run(db, user_id=user.id, work_item=item, max_cycles=payload.max_cycles)
        db.commit(); db.refresh(run)
        return serialize_run(db, run)
    except EngineeringError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/{run_id}", response_model=EngineeringRunRead)
def get_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_run(db, _access(db, user, run_id, "viewer"))

@router.post("/{run_id}/execute-role", response_model=EngineeringRunRead)
async def execute_role(run_id: str, payload: ExecuteRoleRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = _access(db, user, run_id, "member")
    if run.state_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Engineering run state version conflict")
    if run.status != "running" or not run.current_role:
        raise HTTPException(status_code=409, detail="Engineering run has no executable role")
    task = db.get(Task, run.task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="Canonical task not found")
    try:
        ensure_task_compute_available(task, 90)
        quota = ensure_compute_available(db, user, request.app.state.settings, reserve_seconds=90)
        messages, input_sha = build_role_messages(db, run)
    except (TaskBudgetExceededError, QuotaExceededError) as exc:
        db.rollback(); raise HTTPException(status_code=429, detail=str(exc)) from exc
    except EngineeringError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc
    role = run.current_role
    started = perf_counter()
    try:
        async with request.app.state.user_governor.slot(user.id, quota.max_concurrent_inference):
            async with request.app.state.governor.slot():
                raw = await request.app.state.llama.chat(messages, max_tokens=2200, reasoning=role in {"architect", "reviewer"})
    except UserConcurrencyBusyError as exc:
        db.rollback(); raise HTTPException(status_code=429, detail="Account inference limit reached") from exc
    except ResourceBusyError as exc:
        db.rollback(); raise HTTPException(status_code=503, detail="Local inference queue is full") from exc
    except LlamaUnavailable as exc:
        db.rollback(); raise HTTPException(status_code=503, detail="Local engineering inference unavailable") from exc
    inference_ms = int((perf_counter() - started) * 1000)
    usage_kwargs = dict(
        user_id=user.id, project_id=run.project_id, conversation_id=None,
        mode="deep" if role in {"architect", "reviewer"} else "work",
        raw_chars=sum(len(x.content) for x in messages), compiled_chars=sum(len(x.content) for x in messages),
        output_chars=len(raw), duration_ms=inference_ms, inference_ms=inference_ms, queue_ms=0,
    )
    try:
        output = parse_role_output(role, raw)
        persist_role_result(db, run, role=role, input_sha256=input_sha, output=output,
                            model_name=request.app.state.settings.llama_model_name, inference_ms=inference_ms)
        record_task_compute(db, run.task_id, max(1, (inference_ms + 999) // 1000))
        db.add(UsageEvent(**usage_kwargs, success=True))
        db.commit(); db.refresh(run)
        return serialize_run(db, run)
    except EngineeringError as exc:
        # Invalid model output must not advance the role, but compute was still consumed.
        db.rollback()
        fresh_run = db.get(EngineeringRun, run_id)
        if fresh_run is not None:
            record_task_compute(db, fresh_run.task_id, max(1, (inference_ms + 999) // 1000))
            db.add(UsageEvent(**usage_kwargs, success=False))
            db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
