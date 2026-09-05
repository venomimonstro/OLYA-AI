from __future__ import annotations

from pathlib import Path
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Conversation,
    DevelopmentChatSession,
    DevelopmentPlan,
    DevelopmentSprint,
    DevelopmentWorkItem,
    EngineeringExecution,
    EngineeringRun,
    GitOperation,
    GitRepositoryBinding,
    ProjectPreviewSession,
    ProjectRuntime,
    Task,
    utcnow,
)
from app.services.development import activate_sprint, complete_sprint, refresh_sprint_state
from app.services.engineering import create_engineering_run
from app.services.engineering_execution import ExecutionError, create_execution, rollback_changed_files
from app.services.git_collaboration import GitError, changed_paths, commit as git_commit, head


class DevelopmentChatError(RuntimeError):
    pass


_CONTINUE = {
    "продолжи разработку", "продолжить разработку", "следующий спринт", "следующий этап разработки",
    "продолжи следующий спринт", "продолжить следующий спринт", "continue development", "next sprint",
}
_STATUS = {"статус", "статус разработки", "статус проекта", "development status"}
_PAUSE = {"пауза", "поставь разработку на паузу", "приостанови разработку", "pause development"}
_RESUME = {"возобнови разработку", "сними с паузы", "resume development", "продолжи после паузы"}
_ROLLBACK = {"откати последнюю попытку", "откатить последнюю попытку", "rollback last attempt", "откати изменения"}


def detect_command(text: str, explicit: str = "auto") -> str | None:
    if explicit != "auto":
        return explicit
    value = re.sub(r"[.!?]+$", "", text.strip().lower())
    value = re.sub(r"\s+", " ", value)
    if value in _CONTINUE:
        return "continue"
    if value in _STATUS:
        return "status"
    if value in _PAUSE:
        return "pause"
    if value in _RESUME:
        return "resume"
    if value in _ROLLBACK:
        return "rollback"
    return None


def plan_for_project(db: Session, project_id: str) -> DevelopmentPlan:
    plan = db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == project_id))
    if plan is None:
        raise DevelopmentChatError("Development plan is not configured for this project")
    return plan


def get_or_create_session(
    db: Session,
    *,
    project_id: str,
    conversation: Conversation,
    user_id: str,
) -> DevelopmentChatSession:
    if conversation.project_id != project_id:
        raise DevelopmentChatError("Conversation/project mismatch")
    plan = plan_for_project(db, project_id)
    row = db.scalar(select(DevelopmentChatSession).where(DevelopmentChatSession.conversation_id == conversation.id))
    if row is not None:
        if row.project_id != project_id or row.plan_id != plan.id:
            raise DevelopmentChatError("Development chat is bound to another project state")
        return row
    row = DevelopmentChatSession(
        project_id=project_id,
        conversation_id=conversation.id,
        plan_id=plan.id,
        created_by=user_id,
        status="active",
        last_action="status",
        last_summary="Development chat initialized",
    )
    db.add(row)
    db.flush()
    return row


def _active_sprint(db: Session, plan: DevelopmentPlan) -> DevelopmentSprint | None:
    if plan.current_sprint_ordinal is not None:
        return db.scalar(select(DevelopmentSprint).where(
            DevelopmentSprint.plan_id == plan.id,
            DevelopmentSprint.ordinal == plan.current_sprint_ordinal,
        ))
    return db.scalar(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id,
        DevelopmentSprint.status.in_(["active", "verifying"]),
    ).order_by(DevelopmentSprint.ordinal))


def _next_planned_sprint(db: Session, plan: DevelopmentPlan) -> DevelopmentSprint | None:
    sprints = list(db.scalars(select(DevelopmentSprint).where(
        DevelopmentSprint.plan_id == plan.id,
        DevelopmentSprint.status == "planned",
    ).order_by(DevelopmentSprint.ordinal)).all())
    completed = set(db.scalars(select(DevelopmentSprint.ordinal).where(
        DevelopmentSprint.plan_id == plan.id,
        DevelopmentSprint.status == "completed",
    )).all())
    for sprint in sprints:
        if all(dep in completed for dep in (sprint.dependencies or [])):
            return sprint
    return None


def _next_work_item(db: Session, sprint: DevelopmentSprint) -> DevelopmentWorkItem | None:
    items = list(db.scalars(select(DevelopmentWorkItem).where(
        DevelopmentWorkItem.sprint_id == sprint.id
    ).order_by(DevelopmentWorkItem.ordinal)).all())
    completed_ordinals = {x.ordinal for x in items if x.status == "completed"}
    for item in items:
        if item.status == "completed":
            continue
        if all(dep in completed_ordinals for dep in (item.dependencies or [])):
            return item
    return None


def _sync_refs(db: Session, session: DevelopmentChatSession) -> None:
    plan = db.get(DevelopmentPlan, session.plan_id)
    if plan is None:
        return
    sprint = _active_sprint(db, plan)
    session.current_sprint_id = sprint.id if sprint else None
    item = _next_work_item(db, sprint) if sprint else None
    session.current_work_item_id = item.id if item else None
    run = None
    execution = None
    if item:
        run = db.scalar(select(EngineeringRun).where(EngineeringRun.work_item_id == item.id))
        if run:
            execution = db.scalar(select(EngineeringExecution).where(EngineeringExecution.engineering_run_id == run.id))
    session.engineering_run_id = run.id if run else None
    session.execution_id = execution.id if execution else None
    session.updated_at = utcnow()


def _git_state(db: Session, plan: DevelopmentPlan) -> tuple[dict | None, dict | None]:
    if not plan.runtime_id:
        return None, None
    binding = db.scalar(select(GitRepositoryBinding).where(GitRepositoryBinding.runtime_id == plan.runtime_id))
    if binding is None:
        return None, None
    value = {
        "binding_id": binding.id,
        "provider": binding.provider,
        "mode": binding.mode,
        "branch": binding.working_branch or binding.default_branch,
        "local_head": binding.last_local_head,
        "remote_head": binding.last_remote_head,
        "push_enabled": binding.push_enabled,
    }
    approval = None
    if binding.provider == "github" and binding.push_enabled and binding.last_local_head and binding.last_local_head != binding.last_remote_head:
        approval = {
            "kind": "github_push",
            "binding_id": binding.id,
            "branch": binding.working_branch or binding.default_branch,
            "reason": "External GitHub write requires explicit approval",
        }
    return value, approval


def serialize_state(db: Session, session: DevelopmentChatSession) -> dict:
    _sync_refs(db, session)
    plan = db.get(DevelopmentPlan, session.plan_id)
    sprint = db.get(DevelopmentSprint, session.current_sprint_id) if session.current_sprint_id else None
    item = db.get(DevelopmentWorkItem, session.current_work_item_id) if session.current_work_item_id else None
    run = db.get(EngineeringRun, session.engineering_run_id) if session.engineering_run_id else None
    execution = db.get(EngineeringExecution, session.execution_id) if session.execution_id else None
    git_state, approval = _git_state(db, plan) if plan else (None, None)
    preview = None
    if execution:
        row = db.scalar(select(ProjectPreviewSession).where(ProjectPreviewSession.execution_id == execution.id).order_by(ProjectPreviewSession.created_at.desc()))
        if row:
            preview = {"id": row.id, "status": row.status, "public_url": row.public_url, "failure_reason": row.failure_reason}
    return {
        "session_id": session.id,
        "project_id": session.project_id,
        "conversation_id": session.conversation_id,
        "plan_id": session.plan_id,
        "status": session.status,
        "plan_status": plan.status if plan else "missing",
        "sprint": None if sprint is None else {"id": sprint.id, "ordinal": sprint.ordinal, "title": sprint.title, "status": sprint.status},
        "work_item": None if item is None else {"id": item.id, "ordinal": item.ordinal, "title": item.title, "status": item.status, "task_id": item.task_id},
        "engineering": None if run is None else {"id": run.id, "status": run.status, "current_role": run.current_role, "cycle": run.cycle, "state_version": run.state_version},
        "execution": None if execution is None else {"id": execution.id, "status": execution.status, "attempt": execution.attempt, "state_version": execution.state_version, "failure_reason": execution.failure_reason},
        "git": git_state,
        "preview": preview,
        "approval_required": approval,
        "last_action": session.last_action,
        "last_summary": session.last_summary,
        "state_version": session.state_version,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def set_paused(session: DevelopmentChatSession, paused: bool) -> str:
    session.status = "paused" if paused else "active"
    session.last_action = "pause" if paused else "resume"
    session.last_summary = "Development paused for this chat" if paused else "Development resumed for this chat"
    session.updated_at = utcnow()
    return session.last_summary


def prepare_next_step(db: Session, session: DevelopmentChatSession, *, user_id: str) -> tuple[str, object | None]:
    if session.status == "paused":
        return "paused", None
    plan = db.get(DevelopmentPlan, session.plan_id)
    if plan is None:
        raise DevelopmentChatError("Development plan not found")
    if plan.status == "completed":
        return "project_completed", None

    sprint = _active_sprint(db, plan)
    if sprint is None:
        sprint = _next_planned_sprint(db, plan)
        if sprint is None:
            return "blocked", None
        activate_sprint(db, plan, sprint.ordinal, user_id)
        session.current_sprint_id = sprint.id
        session.last_action = "activate_sprint"
        session.last_summary = f"Sprint {sprint.ordinal} activated: {sprint.title}"
        db.flush()
        return "activated_sprint", sprint

    refresh = refresh_sprint_state(db, plan, sprint)
    if refresh["all_completed"]:
        complete_sprint(db, plan, sprint.ordinal)
        session.last_action = "complete_sprint"
        session.last_summary = f"Sprint {sprint.ordinal} completed"
        db.flush()
        return "completed_sprint", sprint

    item = _next_work_item(db, sprint)
    if item is None:
        return "blocked", None
    session.current_sprint_id = sprint.id
    session.current_work_item_id = item.id

    run = db.scalar(select(EngineeringRun).where(EngineeringRun.work_item_id == item.id))
    if run is None:
        run = create_engineering_run(db, user_id=user_id, work_item=item, max_cycles=2)
        session.engineering_run_id = run.id
        session.last_action = "create_engineering_run"
        session.last_summary = f"Engineering team started: {item.title}"
        db.flush()
        return "engineering_created", run
    session.engineering_run_id = run.id

    if run.status == "running":
        return "execute_role", run
    if run.status == "blocked":
        session.last_action = "blocked"
        session.last_summary = f"Engineering review blocked work item: {item.title}"
        return "blocked", run
    if run.status != "approved":
        return "blocked", run

    execution = db.scalar(select(EngineeringExecution).where(EngineeringExecution.engineering_run_id == run.id))
    if execution is None:
        execution = create_execution(db, user_id=user_id, run=run, max_repairs=1)
        session.execution_id = execution.id
        session.last_action = "create_execution"
        session.last_summary = f"Approved implementation prepared: {item.title}"
        db.flush()
        return "execution_created", execution
    session.execution_id = execution.id
    if execution.status in {"planned", "needs_repair"}:
        return "execute_implementation", execution
    if execution.status == "verified":
        return "verified", execution
    if execution.status in {"rolled_back", "blocked"}:
        return "blocked", execution
    return "blocked", execution


def commit_verified_execution(db: Session, session: DevelopmentChatSession, *, user_id: str) -> dict | None:
    if not session.execution_id:
        return None
    execution = db.get(EngineeringExecution, session.execution_id)
    plan = db.get(DevelopmentPlan, session.plan_id)
    item = db.get(DevelopmentWorkItem, session.current_work_item_id) if session.current_work_item_id else None
    sprint = db.get(DevelopmentSprint, session.current_sprint_id) if session.current_sprint_id else None
    if execution is None or execution.status != "verified" or plan is None or not plan.runtime_id:
        return None
    binding = db.scalar(select(GitRepositoryBinding).where(GitRepositoryBinding.runtime_id == plan.runtime_id))
    runtime = db.get(ProjectRuntime, plan.runtime_id)
    if binding is None or runtime is None:
        return None
    from app.models import CodeWorkspace
    workspace = db.get(CodeWorkspace, runtime.workspace_id)
    if workspace is None:
        return None
    root = Path(workspace.root_path)
    paths = [x.get("path") for x in (execution.change_manifest or []) if x.get("path")]
    dirty = set(changed_paths(root))
    paths = [x for x in paths if x in dirty]
    if not paths:
        return None
    before = head(root)
    message = f"Sprint {sprint.ordinal if sprint else '?'}: {item.title if item else 'verified implementation'}"
    try:
        result = git_commit(root, message, paths, expected_head=before)
    except GitError as exc:
        raise DevelopmentChatError(str(exc)) from exc
    op = GitOperation(
        binding_id=binding.id,
        project_id=binding.project_id,
        created_by=user_id,
        kind="commit",
        status="completed",
        branch=binding.working_branch or binding.default_branch,
        head_before=result["head_before"],
        head_after=result["head_after"],
        remote_head=binding.last_remote_head,
        summary=result,
        completed_at=utcnow(),
    )
    db.add(op)
    binding.last_local_head = result["head_after"]
    binding.updated_at = utcnow()
    session.last_action = "local_commit"
    session.last_summary = f"Verified implementation committed locally: {result['head_after'][:8]}"
    db.flush()
    return result


def rollback_latest(db: Session, session: DevelopmentChatSession) -> str:
    execution = None
    if session.execution_id:
        execution = db.get(EngineeringExecution, session.execution_id)
    if execution is None:
        execution = db.scalar(select(EngineeringExecution).where(
            EngineeringExecution.project_id == session.project_id,
            EngineeringExecution.change_manifest != [],
        ).order_by(EngineeringExecution.updated_at.desc()))
    if execution is None or not execution.change_manifest:
        raise DevelopmentChatError("No applied engineering execution is available for rollback")
    if execution.status == "rolled_back":
        raise DevelopmentChatError("Latest engineering execution is already rolled back")
    try:
        rollback_changed_files(db, execution)
    except ExecutionError as exc:
        raise DevelopmentChatError(str(exc)) from exc
    execution.status = "rolled_back"
    execution.failure_reason = "Rolled back from development chat"
    execution.completed_at = utcnow()
    session.execution_id = execution.id
    session.last_action = "rollback"
    session.last_summary = "Latest engineering implementation rolled back to its pre-execution snapshot"
    session.updated_at = utcnow()
    return session.last_summary
