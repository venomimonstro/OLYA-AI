from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DevelopmentPlan, DevelopmentSprint, DevelopmentWorkItem, EngineeringExecution, EngineeringRun, Task
from app.services.deadline import DeadlineExceededError, checkpoint
from app.services.tasks import completion_blockers


class AgentContractError(RuntimeError):
    pass


def agent_completion_blockers(db: Session, plan_id: str) -> list[str]:
    """Independently prove that an autonomous development plan may be called complete.

    The agent ledger never trusts prose, model output, or plan.status alone. Every
    materialized work item must resolve to a completed canonical Task whose own
    evidence gate is green. If engineering execution exists, its latest state must
    be verified rather than planned/failed/rolled back.
    """
    blockers: list[str] = []
    plan = db.get(DevelopmentPlan, plan_id)
    if plan is None:
        return ["plan_missing"]
    if plan.status != "completed":
        blockers.append("plan_not_completed")

    sprints = list(
        db.scalars(
            select(DevelopmentSprint)
            .where(DevelopmentSprint.plan_id == plan.id)
            .order_by(DevelopmentSprint.ordinal)
        ).all()
    )
    if not sprints:
        blockers.append("no_sprints")
        return blockers

    for sprint in sprints:
        if sprint.status != "completed":
            blockers.append(f"sprint:{sprint.id}:not_completed")
        items = list(
            db.scalars(
                select(DevelopmentWorkItem)
                .where(DevelopmentWorkItem.sprint_id == sprint.id)
                .order_by(DevelopmentWorkItem.ordinal)
            ).all()
        )
        if not items:
            blockers.append(f"sprint:{sprint.id}:no_work_items")
            continue
        for item in items:
            if item.status != "completed":
                blockers.append(f"work_item:{item.id}:not_completed")
            if not item.task_id:
                blockers.append(f"work_item:{item.id}:task_missing")
                continue
            task = db.get(Task, item.task_id)
            if task is None:
                blockers.append(f"work_item:{item.id}:task_missing")
                continue
            if task.status != "completed":
                blockers.append(f"task:{task.id}:not_completed")
            for blocker in completion_blockers(db, task):
                blockers.append(f"task:{task.id}:{blocker}")

            runs = list(
                db.scalars(
                    select(EngineeringRun)
                    .where(EngineeringRun.work_item_id == item.id)
                    .order_by(EngineeringRun.updated_at.desc())
                ).all()
            )
            if runs:
                execution = db.scalar(
                    select(EngineeringExecution)
                    .where(EngineeringExecution.engineering_run_id == runs[0].id)
                    .order_by(EngineeringExecution.updated_at.desc())
                    .limit(1)
                )
                if execution is None:
                    blockers.append(f"work_item:{item.id}:execution_missing")
                elif execution.status != "verified":
                    blockers.append(f"execution:{execution.id}:not_verified:{execution.status}")

    return list(dict.fromkeys(blockers))


def require_agent_completion(db: Session, plan_id: str) -> None:
    blockers = agent_completion_blockers(db, plan_id)
    if blockers:
        raise AgentContractError("Agent completion blocked: " + ",".join(blockers[:50]))


def require_agent_progress_budget(stage: str) -> float | None:
    """Fail before launching another autonomous step after request deadline."""
    try:
        return checkpoint(stage)
    except DeadlineExceededError as exc:
        raise AgentContractError(str(exc)) from exc
