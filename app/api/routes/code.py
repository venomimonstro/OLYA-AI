from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CodeAgentRun, CodeWorkspace, Task, User, utcnow
from app.schemas.code import AgentCommand, AgentFileWrite, AgentPlanUpdate, AgentRunCreate, AgentRunRead, WorkspaceCreate, WorkspaceFileWrite, WorkspaceRead
from app.services.access import ROLE_RANK, require_project_role
from app.services.auth import get_current_user
from app.services.code_workspace import WorkspaceError, import_zip, path_allowed, repo_map, resolve_inside, run_command, workspace_root, write_text
from app.services.tasks import require_task_mutation

router = APIRouter(prefix="/v1/code", tags=["code"])


def _workspace_access(db: Session, user: User, workspace_id: str, minimum: str = "viewer") -> CodeWorkspace:
    ws = db.get(CodeWorkspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.project_id:
        require_project_role(db, user, ws.project_id, minimum)
    elif ws.user_id != user.id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def _workspace_write_access(db: Session, user: User, ws: CodeWorkspace) -> CodeWorkspace:
    if ws.project_id:
        _, role = require_project_role(db, user, ws.project_id, "member")
        if ws.user_id != user.id and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Only workspace owner or project manager can modify it")
    elif ws.user_id != user.id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def _run_access(db: Session, user: User, run_id: str, write: bool = False) -> tuple[CodeAgentRun, CodeWorkspace]:
    run = db.get(CodeAgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    ws = _workspace_access(db, user, run.workspace_id, "viewer")
    if write:
        _workspace_write_access(db, user, ws)
        if run.created_by != user.id and (not ws.project_id or require_project_role(db, user, ws.project_id, "manager")):
            pass
    return run, ws


def _sync_stats(ws: CodeWorkspace, root: Path) -> dict:
    mapping = repo_map(root)
    ws.file_count = mapping["file_count"]
    ws.total_bytes = mapping["total_bytes"]
    ws.updated_at = utcnow()
    return mapping


@router.post("/workspaces", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeWorkspace:
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    ws = CodeWorkspace(user_id=user.id, project_id=payload.project_id, name=payload.name.strip(), root_path="")
    db.add(ws)
    db.flush()
    root = workspace_root(request.app.state.settings.code_workspace_storage_path, ws.id)
    root.mkdir(parents=True, exist_ok=False)
    ws.root_path = str(root)
    db.commit()
    db.refresh(ws)
    return ws


@router.get("/workspaces", response_model=list[WorkspaceRead])
def list_workspaces(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CodeWorkspace]:
    rows = list(db.scalars(select(CodeWorkspace).where(CodeWorkspace.user_id == user.id).order_by(CodeWorkspace.updated_at.desc())).all())
    # Shared project workspaces are added conservatively to avoid broad joins in the MVP.
    from app.services.access import list_accessible_projects
    project_ids = [p.id for p in list_accessible_projects(db, user.id)]
    if project_ids:
        shared = list(db.scalars(select(CodeWorkspace).where(CodeWorkspace.project_id.in_(project_ids))).all())
        seen = {x.id for x in rows}
        rows.extend(x for x in shared if x.id not in seen)
    return sorted(rows, key=lambda x: x.updated_at, reverse=True)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeWorkspace:
    return _workspace_access(db, user, workspace_id, "viewer")


@router.post("/workspaces/{workspace_id}/import-zip", response_model=WorkspaceRead)
async def import_workspace_zip(workspace_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeWorkspace:
    ws = _workspace_access(db, user, workspace_id, "viewer")
    _workspace_write_access(db, user, ws)
    body = await request.body()
    if len(body) > request.app.state.settings.code_workspace_max_archive_bytes:
        raise HTTPException(status_code=413, detail="Workspace archive too large")
    root = Path(ws.root_path).resolve()
    try:
        stats = import_zip(
            root,
            body,
            max_files=request.app.state.settings.code_workspace_max_files,
            max_unpacked_bytes=request.app.state.settings.code_workspace_max_unpacked_bytes,
        )
    except (WorkspaceError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ws.file_count = stats["file_count"]
    ws.total_bytes = stats["total_bytes"]
    ws.updated_at = utcnow()
    db.commit()
    db.refresh(ws)
    return ws


@router.get("/workspaces/{workspace_id}/map")
def workspace_map(workspace_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    ws = _workspace_access(db, user, workspace_id, "viewer")
    return repo_map(Path(ws.root_path).resolve())


@router.put("/workspaces/{workspace_id}/file")
def workspace_write_file(workspace_id: str, payload: WorkspaceFileWrite, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    ws = _workspace_access(db, user, workspace_id, "viewer")
    _workspace_write_access(db, user, ws)
    try:
        result = write_text(Path(ws.root_path).resolve(), payload.path, payload.content, payload.expected_sha256)
    except WorkspaceError as exc:
        raise HTTPException(status_code=409 if "changed" in str(exc).lower() else 422, detail=str(exc)) from exc
    _sync_stats(ws, Path(ws.root_path).resolve())
    db.commit()
    return result


@router.get("/workspaces/{workspace_id}/file")
def workspace_read_file(workspace_id: str, path: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    ws = _workspace_access(db, user, workspace_id, "viewer")
    try:
        target = resolve_inside(Path(ws.root_path).resolve(), path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not target.is_file() or target.stat().st_size > 2_000_000:
        raise HTTPException(status_code=404, detail="File not found or too large")
    return Response(content=target.read_bytes(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.post("/workspaces/{workspace_id}/agent-runs", response_model=AgentRunRead, status_code=status.HTTP_201_CREATED)
def create_agent_run(workspace_id: str, payload: AgentRunCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    ws = _workspace_access(db, user, workspace_id, "viewer")
    _workspace_write_access(db, user, ws)
    if payload.task_id:
        task, _ = require_task_mutation(db, user, payload.task_id)
        if task.project_id != ws.project_id:
            raise HTTPException(status_code=400, detail="Task and workspace must belong to the same project")
    # Validate path scope now, before the run can be used.
    from app.services.code_workspace import safe_relative_path
    allowed = [safe_relative_path(x).as_posix() for x in payload.allowed_paths]
    run = CodeAgentRun(
        workspace_id=ws.id,
        task_id=payload.task_id,
        created_by=user.id,
        goal=payload.goal.strip(),
        allowed_paths=allowed,
        max_commands=payload.max_commands,
        checkpoint={"phase": "planned", "repo_map": repo_map(Path(ws.root_path).resolve())},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.get("/agent-runs/{run_id}", response_model=AgentRunRead)
def get_agent_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    run, _ = _run_access(db, user, run_id)
    return run


@router.patch("/agent-runs/{run_id}/plan", response_model=AgentRunRead)
def update_agent_plan(run_id: str, payload: AgentPlanUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    run, _ = _run_access(db, user, run_id, write=True)
    if run.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Terminal agent run is immutable")
    run.plan = payload.plan
    run.checkpoint = payload.checkpoint
    run.status = "running"
    run.updated_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


@router.put("/agent-runs/{run_id}/file", response_model=AgentRunRead)
def agent_write_file(run_id: str, payload: AgentFileWrite, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    run, ws = _run_access(db, user, run_id, write=True)
    if run.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Terminal agent run is immutable")
    if not path_allowed(payload.path, run.allowed_paths):
        raise HTTPException(status_code=403, detail="Path is outside approved agent scope")
    try:
        change = write_text(Path(ws.root_path).resolve(), payload.path, payload.content, payload.expected_sha256)
    except WorkspaceError as exc:
        raise HTTPException(status_code=409 if "changed" in str(exc).lower() else 422, detail=str(exc)) from exc
    run.changed_files = [*run.changed_files, change]
    run.checkpoint = {**(run.checkpoint or {}), "phase": "editing", "last_change": change}
    run.status = "running"
    run.updated_at = utcnow()
    _sync_stats(ws, Path(ws.root_path).resolve())
    db.commit()
    db.refresh(run)
    return run


@router.post("/agent-runs/{run_id}/command", response_model=AgentRunRead)
def agent_run_command(run_id: str, payload: AgentCommand, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    run, ws = _run_access(db, user, run_id, write=True)
    if run.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Terminal agent run is immutable")
    if run.commands_used >= run.max_commands:
        raise HTTPException(status_code=409, detail="Agent command budget exhausted")
    try:
        result = run_command(
            Path(ws.root_path).resolve(),
            payload.argv,
            payload.timeout_seconds,
            allow_unsafe=bool(request.app.state.settings.code_allow_unsafe_commands),
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    run.commands_used += 1
    run.command_results = [*run.command_results, result]
    run.checkpoint = {**(run.checkpoint or {}), "phase": "verification", "last_command": result}
    run.status = "running" if result.get("exit_code") in {0, None} else "blocked"
    run.updated_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


@router.post("/agent-runs/{run_id}/complete", response_model=AgentRunRead)
def complete_agent_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CodeAgentRun:
    run, _ = _run_access(db, user, run_id, write=True)
    if run.status in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Agent run cannot be completed")
    if run.command_results and any((x.get("timed_out") or x.get("exit_code") not in {0}) for x in run.command_results[-3:]):
        raise HTTPException(status_code=409, detail="Recent verification command failed")
    run.status = "completed"
    run.checkpoint = {**(run.checkpoint or {}), "phase": "completed"}
    run.updated_at = utcnow()
    db.commit()
    db.refresh(run)
    return run
