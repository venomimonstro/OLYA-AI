from __future__ import annotations

import hashlib
import json

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    ArchitectureDecision,
    DevelopmentCheckpoint,
    DevelopmentPlan,
    DevelopmentSprint,
    DevelopmentWorkItem,
    ProjectRuntime,
    ProjectRuntimeSnapshot,
    Task,
    TaskCriterion,
    utcnow,
)


class DevelopmentError(ValueError):
    pass


def normalize_requirements(items) -> list[dict]:
    result = []
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        result.append({
            "key": str(data["key"]).strip(),
            "text": str(data["text"]).strip(),
            "priority": str(data.get("priority", "must")),
        })
    return result


def serialize_plan(db: Session, plan: DevelopmentPlan) -> dict:
    sprints = list(db.scalars(
        select(DevelopmentSprint).where(DevelopmentSprint.plan_id == plan.id).order_by(DevelopmentSprint.ordinal)
    ).all())
    sprint_rows = []
    for sprint in sprints:
        items = list(db.scalars(
            select(DevelopmentWorkItem).where(DevelopmentWorkItem.sprint_id == sprint.id).order_by(DevelopmentWorkItem.ordinal)
        ).all())
        sprint_rows.append({
            "id": sprint.id,
            "ordinal": sprint.ordinal,
            "title": sprint.title,
            "goal": sprint.goal,
            "status": sprint.status,
            "dependencies": sprint.dependencies or [],
            "acceptance_criteria": sprint.acceptance_criteria or [],
            "items": [{
                "id": item.id,
                "ordinal": item.ordinal,
                "title": item.title,
                "goal": item.goal,
                "kind": item.kind,
                "status": item.status,
                "dependencies": item.dependencies or [],
                "acceptance_criteria": item.acceptance_criteria or [],
                "task_id": item.task_id,
            } for item in items],
            "started_at": sprint.started_at,
            "completed_at": sprint.completed_at,
        })
    decisions = list(db.scalars(
        select(ArchitectureDecision).where(ArchitectureDecision.plan_id == plan.id).order_by(ArchitectureDecision.created_at)
    ).all())
    return {
        "id": plan.id,
        "project_id": plan.project_id,
        "runtime_id": plan.runtime_id,
        "created_by": plan.created_by,
        "title": plan.title,
        "product_brief": plan.product_brief,
        "requirements": plan.requirements or [],
        "architecture": plan.architecture or {},
        "constraints": plan.constraints or [],
        "status": plan.status,
        "current_sprint_ordinal": plan.current_sprint_ordinal,
        "state_version": plan.state_version,
        "sprints": sprint_rows,
        "decisions": [{
            "id": row.id,
            "key": row.key,
            "title": row.title,
            "decision": row.decision,
            "rationale": row.rationale,
            "status": row.status,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        } for row in decisions],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def validate_runtime_binding(db: Session, project_id: str, runtime_id: str | None) -> None:
    if runtime_id is None:
        return
    runtime = db.get(ProjectRuntime, runtime_id)
    if runtime is None or runtime.project_id != project_id:
        raise DevelopmentError("Runtime does not belong to project")


def create_plan(db: Session, *, user_id: str, payload) -> DevelopmentPlan:
    existing = db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == payload.project_id))
    if existing is not None:
        raise DevelopmentError("Development plan already exists")
    validate_runtime_binding(db, payload.project_id, payload.runtime_id)
    plan = DevelopmentPlan(
        project_id=payload.project_id,
        runtime_id=payload.runtime_id,
        created_by=user_id,
        title=payload.title.strip(),
        product_brief=payload.product_brief.strip(),
        requirements=normalize_requirements(payload.requirements),
        architecture=payload.architecture,
        constraints=payload.constraints,
    )
    db.add(plan)
    db.flush()
    for sprint_payload in sorted(payload.sprints, key=lambda x: x.ordinal):
        _add_sprint_from_payload(db, plan.id, sprint_payload)
    return plan



def _add_sprint_from_payload(db: Session, plan_id: str, sprint_payload) -> DevelopmentSprint:
    sprint = DevelopmentSprint(
        plan_id=plan_id, ordinal=sprint_payload.ordinal, title=sprint_payload.title.strip(), goal=sprint_payload.goal.strip(),
        dependencies=sprint_payload.dependencies, acceptance_criteria=[x.strip() for x in sprint_payload.acceptance_criteria if x.strip()],
    )
    db.add(sprint); db.flush()
    covered = set(); max_ordinal = 0
    for item_payload in sorted(sprint_payload.items, key=lambda x: x.ordinal):
        criteria = [x.strip() for x in item_payload.acceptance_criteria if x.strip()]
        covered.update(criteria); max_ordinal = max(max_ordinal, item_payload.ordinal)
        db.add(DevelopmentWorkItem(
            sprint_id=sprint.id, ordinal=item_payload.ordinal, title=item_payload.title.strip(), goal=item_payload.goal.strip(),
            kind=item_payload.kind, dependencies=item_payload.dependencies, acceptance_criteria=criteria,
        ))
    uncovered = [x.strip() for x in sprint_payload.acceptance_criteria if x.strip() and x.strip() not in covered]
    if uncovered:
        db.add(DevelopmentWorkItem(
            sprint_id=sprint.id, ordinal=max_ordinal + 1, title="Sprint acceptance verification",
            goal=f"Verify sprint {sprint.ordinal} acceptance criteria before completion", kind="test",
            dependencies=[x.ordinal for x in sprint_payload.items], acceptance_criteria=uncovered,
        ))
    return sprint


def replan_future_sprints(db: Session, plan: DevelopmentPlan, future_sprints, *, user_id: str, reason: str) -> DevelopmentCheckpoint:
    frozen = list(db.scalars(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id, DevelopmentSprint.status != "planned"
    ).order_by(DevelopmentSprint.ordinal)).all())
    frozen_ordinals = {x.ordinal for x in frozen}
    floor = max(frozen_ordinals, default=0)
    incoming = sorted(future_sprints, key=lambda x: x.ordinal)
    incoming_ordinals = {x.ordinal for x in incoming}
    if any(x.ordinal <= floor for x in incoming):
        raise DevelopmentError("Replan may only replace future unstarted sprints")
    allowed_dependencies = frozen_ordinals | incoming_ordinals
    for sprint in incoming:
        if any(dep not in allowed_dependencies or dep >= sprint.ordinal for dep in sprint.dependencies):
            raise DevelopmentError("Replanned sprint has invalid dependency")
    checkpoint = create_checkpoint(db, plan, user_id=user_id, reason="before replan: " + reason[:160], runtime_snapshot_id=None)
    planned = list(db.scalars(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id, DevelopmentSprint.status == "planned"
    )).all())
    for sprint in planned:
        db.execute(delete(DevelopmentWorkItem).where(DevelopmentWorkItem.sprint_id == sprint.id))
        db.delete(sprint)
    db.flush()
    for sprint_payload in incoming:
        _add_sprint_from_payload(db, plan.id, sprint_payload)
    plan.updated_at = utcnow()
    return checkpoint

def _sprint_by_ordinal(db: Session, plan_id: str, ordinal: int) -> DevelopmentSprint:
    sprint = db.scalar(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan_id,
        DevelopmentSprint.ordinal == ordinal,
    ))
    if sprint is None:
        raise DevelopmentError("Sprint not found")
    return sprint


def _sync_item_status(db: Session, item: DevelopmentWorkItem) -> str:
    if not item.task_id:
        return item.status
    task = db.get(Task, item.task_id)
    if task is None:
        item.task_id = None
        item.status = "planned"
    elif task.status == "completed":
        item.status = "completed"
    elif task.status in {"failed", "cancelled"}:
        item.status = "blocked"
    elif task.status in {"running", "waiting", "verifying"}:
        item.status = "active"
    return item.status


def materialize_sprint_tasks(db: Session, plan: DevelopmentPlan, sprint: DevelopmentSprint, user_id: str) -> list[Task]:
    items = list(db.scalars(select(DevelopmentWorkItem).where(
        DevelopmentWorkItem.sprint_id == sprint.id
    ).order_by(DevelopmentWorkItem.ordinal)).all())
    created: list[Task] = []
    for item in items:
        if item.task_id:
            task = db.get(Task, item.task_id)
            if task is not None:
                created.append(task)
                continue
        criteria = item.acceptance_criteria or sprint.acceptance_criteria
        task = Task(
            project_id=plan.project_id,
            created_by=user_id,
            title=f"Sprint {sprint.ordinal}: {item.title}",
            goal=item.goal,
            constraints=list(plan.constraints or []),
            max_steps=max(10, min(100, len(criteria) * 8)),
            max_compute_seconds=1800,
        )
        db.add(task)
        db.flush()
        for idx, text in enumerate(criteria, start=1):
            db.add(TaskCriterion(
                task_id=task.id,
                ordinal=idx,
                text=text,
                required=True,
                verification_method="evidence",
            ))
        item.task_id = task.id
        item.status = "active"
        created.append(task)
    return created


def activate_sprint(db: Session, plan: DevelopmentPlan, ordinal: int, user_id: str) -> DevelopmentSprint:
    sprint = _sprint_by_ordinal(db, plan.id, ordinal)
    for dependency in sprint.dependencies or []:
        dep = _sprint_by_ordinal(db, plan.id, dependency)
        if dep.status != "completed":
            raise DevelopmentError(f"Sprint dependency {dependency} is not completed")
    active = db.scalar(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id,
        DevelopmentSprint.status.in_(["active", "verifying"]),
        DevelopmentSprint.id != sprint.id,
    ))
    if active is not None:
        raise DevelopmentError(f"Sprint {active.ordinal} is already active")
    if sprint.status == "completed":
        raise DevelopmentError("Completed sprint cannot be reactivated")
    sprint.status = "active"
    sprint.started_at = sprint.started_at or utcnow()
    plan.status = "active"
    plan.current_sprint_ordinal = sprint.ordinal
    plan.updated_at = utcnow()
    materialize_sprint_tasks(db, plan, sprint, user_id)
    return sprint


def refresh_sprint_state(db: Session, plan: DevelopmentPlan, sprint: DevelopmentSprint) -> dict:
    items = list(db.scalars(select(DevelopmentWorkItem).where(
        DevelopmentWorkItem.sprint_id == sprint.id
    ).order_by(DevelopmentWorkItem.ordinal)).all())
    statuses = [_sync_item_status(db, item) for item in items]
    all_completed = bool(items) and all(x == "completed" for x in statuses)
    if all_completed and sprint.status in {"active", "verifying"}:
        sprint.status = "verifying"
    return {"all_completed": all_completed, "items": items}


def complete_sprint(db: Session, plan: DevelopmentPlan, ordinal: int) -> DevelopmentSprint:
    sprint = _sprint_by_ordinal(db, plan.id, ordinal)
    state = refresh_sprint_state(db, plan, sprint)
    if not state["all_completed"]:
        raise DevelopmentError("Sprint has incomplete work items")
    sprint.status = "completed"
    sprint.completed_at = utcnow()
    remaining = list(db.scalars(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id,
        DevelopmentSprint.status != "completed",
    ).order_by(DevelopmentSprint.ordinal)).all())
    if remaining:
        plan.current_sprint_ordinal = None
        plan.status = "active"
    else:
        plan.current_sprint_ordinal = None
        plan.status = "completed"
    plan.updated_at = utcnow()
    return sprint


def add_decision(db: Session, plan: DevelopmentPlan, *, user_id: str, key: str, title: str, decision: str, rationale: str) -> ArchitectureDecision:
    existing = db.scalar(select(ArchitectureDecision).where(
        ArchitectureDecision.plan_id == plan.id,
        ArchitectureDecision.key == key.strip(),
    ))
    if existing is not None:
        raise DevelopmentError("Architecture decision key already exists")
    row = ArchitectureDecision(
        plan_id=plan.id,
        key=key.strip(),
        title=title.strip(),
        decision=decision.strip(),
        rationale=rationale.strip(),
        created_by=user_id,
    )
    db.add(row)
    plan.updated_at = utcnow()
    return row


def _checkpoint_state(db: Session, plan: DevelopmentPlan) -> dict:
    data = serialize_plan(db, plan)
    return {
        "plan_id": data["id"],
        "project_id": data["project_id"],
        "title": data["title"],
        "status": data["status"],
        "state_version": data["state_version"],
        "current_sprint_ordinal": data["current_sprint_ordinal"],
        "requirements": data["requirements"],
        "architecture": data["architecture"],
        "constraints": data["constraints"],
        "sprints": data["sprints"],
        "decisions": data["decisions"],
    }


def create_checkpoint(db: Session, plan: DevelopmentPlan, *, user_id: str, reason: str, runtime_snapshot_id: str | None) -> DevelopmentCheckpoint:
    if runtime_snapshot_id is not None:
        snap = db.get(ProjectRuntimeSnapshot, runtime_snapshot_id)
        if snap is None or plan.runtime_id is None or snap.runtime_id != plan.runtime_id:
            raise DevelopmentError("Runtime snapshot does not belong to plan runtime")
    state = _checkpoint_state(db, plan)
    safe_state = json.loads(json.dumps(state, default=str, ensure_ascii=False))
    canonical = json.dumps(safe_state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    existing = db.scalar(select(DevelopmentCheckpoint).where(
        DevelopmentCheckpoint.plan_id == plan.id,
        DevelopmentCheckpoint.state_sha256 == digest,
        DevelopmentCheckpoint.runtime_snapshot_id == runtime_snapshot_id,
    ))
    if existing is not None:
        return existing
    sequence = (db.scalar(select(func.max(DevelopmentCheckpoint.sequence)).where(DevelopmentCheckpoint.plan_id == plan.id)) or 0) + 1
    row = DevelopmentCheckpoint(
        plan_id=plan.id,
        sequence=sequence,
        plan_state_version=plan.state_version,
        current_sprint_ordinal=plan.current_sprint_ordinal,
        runtime_snapshot_id=runtime_snapshot_id,
        state_sha256=digest,
        state=safe_state,
        reason=reason.strip() or "manual",
        created_by=user_id,
    )
    db.add(row)
    return row



def extract_json_object(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        raise DevelopmentError("Architect returned invalid JSON")
    try:
        data = json.loads(value[start:end + 1])
    except json.JSONDecodeError as exc:
        raise DevelopmentError("Architect returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise DevelopmentError("Architect returned invalid plan")
    return data


def architect_messages(product_brief: str, constraints: list[str], target_sprints: int):
    from app.schemas.chat import ChatMessage
    schema = {
        "title": "short project title",
        "requirements": [{"key": "REQ-1", "text": "requirement", "priority": "must"}],
        "architecture": {"summary": "architecture", "stack": ["technology"], "components": ["component"]},
        "sprints": [{
            "ordinal": 1, "title": "sprint title", "goal": "goal", "dependencies": [],
            "acceptance_criteria": ["sprint criterion"],
            "items": [{"ordinal": 1, "title": "work item", "goal": "goal", "kind": "feature", "dependencies": [], "acceptance_criteria": ["verifiable criterion"]}]
        }]
    }
    system = (
        "You are X1 Project Architect. Produce ONLY one JSON object, no markdown. "
        "Create an implementation-ready development plan. Do not invent external services as mandatory dependencies. "
        "Every sprint and work item must have observable acceptance criteria. Dependencies may only point backward. "
        "Keep the plan economical for a single-machine local-first environment unless the user explicitly requests otherwise. "
        f"Target approximately {target_sprints} sprints. Required JSON shape: {json.dumps(schema, ensure_ascii=False)}"
    )
    user = "PROJECT BRIEF:\n" + product_brief.strip()
    if constraints:
        user += "\n\nCONSTRAINTS:\n" + "\n".join(f"- {x.strip()}" for x in constraints if x.strip())
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]

def compact_project_development_context(db: Session, project_id: str) -> str:
    plan = db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == project_id))
    if plan is None:
        return ""
    lines = [
        "Canonical development plan:",
        f"Plan: {plan.title}",
        f"Plan status: {plan.status}",
        f"Plan version: {plan.state_version}",
    ]
    if plan.current_sprint_ordinal is not None:
        sprint = db.scalar(select(DevelopmentSprint).where(
            DevelopmentSprint.plan_id == plan.id,
            DevelopmentSprint.ordinal == plan.current_sprint_ordinal,
        ))
        if sprint is not None:
            items = list(db.scalars(select(DevelopmentWorkItem).where(
                DevelopmentWorkItem.sprint_id == sprint.id
            ).order_by(DevelopmentWorkItem.ordinal)).all())
            lines.extend([
                f"Current sprint: {sprint.ordinal}. {sprint.title}",
                f"Sprint goal: {sprint.goal}",
                "Sprint work items:",
            ])
            for item in items[:30]:
                lines.append(f"- [{item.status}] {item.ordinal}. {item.title}")
    decisions = list(db.scalars(select(ArchitectureDecision).where(
        ArchitectureDecision.plan_id == plan.id,
        ArchitectureDecision.status == "active",
    ).order_by(ArchitectureDecision.created_at.desc()).limit(12)).all())
    if decisions:
        lines.append("Active architecture decisions:")
        for row in reversed(decisions):
            lines.append(f"- {row.key}: {row.decision}")
    return "\n".join(lines)
