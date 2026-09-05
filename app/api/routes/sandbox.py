from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CodeWorkspace, EngineeringExecution, EngineeringRun, ProjectRuntime, ProjectSandboxRun, User, utcnow
from app.schemas.engineering import ProjectSandboxRunRead, SandboxRunCreate, SandboxRunExecute
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.engineering_execution import _approved_scope
from app.services.sandbox import SandboxError, run_in_container, sandbox_capabilities

router = APIRouter(prefix="/v1/project-sandboxes", tags=["project-sandbox"])


def _access(db: Session, user: User, row_id: str, minimum: str = "viewer") -> ProjectSandboxRun:
    row = db.get(ProjectSandboxRun, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sandbox run not found")
    require_project_role(db, user, row.project_id, minimum)
    return row


@router.get("/capabilities")
def capabilities(request: Request, user: User = Depends(get_current_user)):
    _ = user
    st = request.app.state.settings
    return sandbox_capabilities(st.project_sandbox_backend, st.project_sandbox_image)


@router.post("", response_model=ProjectSandboxRunRead, status_code=status.HTTP_201_CREATED)
def create_run(payload: SandboxRunCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ex = db.get(EngineeringExecution, payload.execution_id)
    if ex is None:
        raise HTTPException(status_code=404, detail="Engineering execution not found")
    require_project_role(db, user, ex.project_id, "member")
    eng = db.get(EngineeringRun, ex.engineering_run_id)
    if eng is None or eng.status != "approved":
        raise HTTPException(status_code=409, detail="Approved engineering run required")
    runtime = db.get(ProjectRuntime, ex.runtime_id)
    if runtime is None:
        raise HTTPException(status_code=409, detail="Project runtime missing")
    _, _, commands = _approved_scope(db, eng)
    caps = sandbox_capabilities(request.app.state.settings.project_sandbox_backend, request.app.state.settings.project_sandbox_image)
    row = ProjectSandboxRun(project_id=ex.project_id, runtime_id=runtime.id, execution_id=ex.id, created_by=user.id,
                            backend=caps["backend"], image=request.app.state.settings.project_sandbox_image,
                            network_policy=runtime.network_policy, commands=commands, capability_snapshot=caps,
                            status="planned" if caps["available"] else "unavailable",
                            failure_reason="" if caps["available"] else caps["reason"])
    db.add(row); db.commit(); db.refresh(row)
    return row


@router.post("/{run_id}/execute", response_model=ProjectSandboxRunRead)
def execute_run(run_id: str, payload: SandboxRunExecute, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _access(db, user, run_id, "member")
    if row.state_version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Sandbox run state version conflict")
    if row.status not in {"planned", "failed"}:
        raise HTTPException(status_code=409, detail=f"Sandbox run cannot execute from {row.status}")
    runtime = db.get(ProjectRuntime, row.runtime_id); ex = db.get(EngineeringExecution, row.execution_id) if row.execution_id else None
    workspace = db.get(CodeWorkspace, runtime.workspace_id) if runtime else None
    if runtime is None or workspace is None:
        raise HTTPException(status_code=409, detail="Sandbox runtime/workspace missing")
    caps = sandbox_capabilities(request.app.state.settings.project_sandbox_backend, request.app.state.settings.project_sandbox_image)
    row.capability_snapshot = caps; row.backend = caps["backend"]
    if not caps["available"]:
        row.status="unavailable"; row.failure_reason=caps["reason"]; db.commit(); db.refresh(row)
        raise HTTPException(status_code=503, detail=row.failure_reason)
    row.status="running"; row.started_at=utcnow(); row.failure_reason=""; db.commit(); db.refresh(row)
    results=[]
    try:
        for argv in row.commands or []:
            result = run_in_container(preferred_backend=request.app.state.settings.project_sandbox_backend,
                                      image=request.app.state.settings.project_sandbox_image,
                                      workspace=Path(workspace.root_path), scratch=Path(runtime.runtime_root)/"sandbox"/row.id,
                                      argv=argv, timeout_seconds=request.app.state.settings.project_sandbox_command_timeout_seconds,
                                      cpu_limit=runtime.cpu_limit, memory_mb=runtime.memory_limit_mb,
                                      process_limit=runtime.process_limit, network_policy=runtime.network_policy)
            results.append(result)
            if result.get("timed_out") or result.get("exit_code") != 0:
                row.status="failed"; row.results=results; row.failure_reason="Sandbox verification command failed"; row.completed_at=utcnow()
                db.commit(); db.refresh(row); return row
    except SandboxError as exc:
        row.status="failed"; row.results=results; row.failure_reason=str(exc); row.completed_at=utcnow(); db.commit(); db.refresh(row)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    row.results=results; row.status="passed"; row.completed_at=utcnow(); db.commit(); db.refresh(row)
    return row

from datetime import timedelta
from app.models import ProjectPreviewSession
from app.schemas.engineering import PreviewCreate, ProjectPreviewRead
from app.services.sandbox import start_detached_preview, exec_in_container, stop_container


@router.post("/previews", response_model=ProjectPreviewRead, status_code=status.HTTP_201_CREATED)
def start_preview(payload: PreviewCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ex = db.get(EngineeringExecution, payload.execution_id)
    if ex is None:
        raise HTTPException(status_code=404, detail="Engineering execution not found")
    require_project_role(db, user, ex.project_id, "member")
    if ex.status != "verified":
        raise HTTPException(status_code=409, detail="Verified engineering execution required")
    eng = db.get(EngineeringRun, ex.engineering_run_id); runtime = db.get(ProjectRuntime, ex.runtime_id)
    workspace = db.get(CodeWorkspace, ex.workspace_id)
    dev = dict((eng.handoff_state or {}).get("developer") or {}) if eng else {}
    command = dev.get("preview_command") or []
    health_command = dev.get("preview_health_command") or []
    port = int(dev.get("preview_port") or 0)
    row = ProjectPreviewSession(project_id=ex.project_id, runtime_id=ex.runtime_id, execution_id=ex.id,
                                created_by=user.id, command=command, internal_port=port,
                                health_spec={"command": health_command}, status="starting")
    db.add(row); db.flush()
    if not command or not health_command or not port:
        row.status="unavailable"; row.failure_reason="Approved preview command, port and health command are required"
        db.commit(); db.refresh(row); return row
    caps = sandbox_capabilities(request.app.state.settings.project_sandbox_backend, request.app.state.settings.project_sandbox_image)
    if not caps["available"] or runtime is None or workspace is None:
        row.status="unavailable"; row.failure_reason=caps.get("reason") or "Sandbox unavailable"
        db.commit(); db.refresh(row); return row
    name = "x1-preview-" + row.id.replace("-", "")[:20]
    try:
        started = start_detached_preview(preferred_backend=request.app.state.settings.project_sandbox_backend,
            image=request.app.state.settings.project_sandbox_image, workspace=Path(workspace.root_path),
            scratch=Path(runtime.runtime_root)/"preview"/row.id, argv=command, name=name,
            cpu_limit=runtime.cpu_limit, memory_mb=runtime.memory_limit_mb, process_limit=runtime.process_limit,
            network_policy="deny")
        row.container_ref=started["container_ref"]
        check = exec_in_container(preferred_backend=request.app.state.settings.project_sandbox_backend,
                                  container_ref=row.container_ref, argv=health_command,
                                  timeout_seconds=request.app.state.settings.project_sandbox_preview_timeout_seconds)
        if check["exit_code"] != 0:
            stop_container(preferred_backend=request.app.state.settings.project_sandbox_backend, container_ref=row.container_ref)
            row.status="failed"; row.failure_reason=(check.get("stderr") or "Preview health check failed")[-4000:]; row.container_ref=""
        else:
            # Preview is healthy but intentionally not exposed outside the isolated container yet.
            row.status="healthy_internal"; row.public_url=""; row.expires_at=utcnow()+timedelta(minutes=15)
    except SandboxError as exc:
        if row.container_ref:
            stop_container(preferred_backend=request.app.state.settings.project_sandbox_backend, container_ref=row.container_ref)
        row.status="failed"; row.failure_reason=str(exc); row.container_ref=""
    db.commit(); db.refresh(row); return row


@router.post("/previews/{preview_id}/stop", response_model=ProjectPreviewRead)
def stop_preview(preview_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(ProjectPreviewSession, preview_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Preview not found")
    require_project_role(db, user, row.project_id, "member")
    if row.container_ref:
        stop_container(preferred_backend=request.app.state.settings.project_sandbox_backend, container_ref=row.container_ref)
    row.container_ref=""; row.status="stopped"; row.updated_at=utcnow(); db.commit(); db.refresh(row); return row
