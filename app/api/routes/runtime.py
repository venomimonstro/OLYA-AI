from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CodeWorkspace, GitRepositoryBinding, ProjectRuntime, ProjectRuntimeSecret, ProjectRuntimeSnapshot, User, utcnow
from app.schemas.runtime import RuntimeCreate, RuntimeRead, RuntimeSecretRead, RuntimeSecretWrite, RuntimeSnapshotRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.project_runtime import RuntimeError, build_manifest, create_snapshot, detect_isolation_backend, encrypt_secret, runtime_root
from app.services.git_collaboration import GitError, ensure_local_repo

router = APIRouter(prefix="/v1/project-runtimes", tags=["project-runtime"])


def _runtime_access(db: Session, user: User, runtime_id: str, minimum: str = "viewer") -> ProjectRuntime:
    rt = db.get(ProjectRuntime, runtime_id)
    if rt is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    require_project_role(db, user, rt.project_id, minimum)
    return rt


@router.post("", response_model=RuntimeRead, status_code=status.HTTP_201_CREATED)
def create_runtime(payload: RuntimeCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ws = db.get(CodeWorkspace, payload.workspace_id)
    if ws is None or not ws.project_id:
        raise HTTPException(status_code=400, detail="Project workspace required")
    require_project_role(db, user, ws.project_id, "manager")
    existing = db.scalar(select(ProjectRuntime).where(ProjectRuntime.project_id == ws.project_id))
    if existing:
        raise HTTPException(status_code=409, detail="Project runtime already exists")
    try:
        git_state = ensure_local_repo(Path(ws.root_path), "main")
    except GitError as exc:
        raise HTTPException(status_code=503, detail=f"Local Git is required for project runtime: {exc}") from exc
    rt = ProjectRuntime(project_id=ws.project_id, workspace_id=ws.id, created_by=user.id, runtime_root="",
                        cpu_limit=payload.cpu_limit, memory_limit_mb=payload.memory_limit_mb,
                        disk_limit_mb=payload.disk_limit_mb, process_limit=payload.process_limit,
                        network_policy=payload.network_policy)
    db.add(rt); db.flush()
    root = runtime_root(request.app.state.settings.project_runtime_storage_path, rt.id)
    root.mkdir(parents=True, exist_ok=False)
    isolation = detect_isolation_backend()
    rt.runtime_root = str(root)
    rt.isolation_backend = isolation["backend"]
    rt.manifest = build_manifest(Path(ws.root_path), project_id=ws.project_id, workspace_id=ws.id,
                                 cpu_limit=rt.cpu_limit, memory_mb=rt.memory_limit_mb, disk_mb=rt.disk_limit_mb,
                                 process_limit=rt.process_limit, network_policy=rt.network_policy) | {"isolation": isolation, "git": git_state}
    db.add(GitRepositoryBinding(project_id=ws.project_id, runtime_id=rt.id, workspace_id=ws.id, created_by=user.id,
                                provider="local", default_branch=git_state.get("branch") or "main", mode="local_only",
                                status="ready", last_local_head=git_state.get("head", "")))
    db.commit(); db.refresh(rt)
    return rt


@router.get("/{runtime_id}", response_model=RuntimeRead)
def get_runtime(runtime_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _runtime_access(db, user, runtime_id)


@router.post("/{runtime_id}/snapshots", response_model=RuntimeSnapshotRead, status_code=status.HTTP_201_CREATED)
def snapshot_runtime(runtime_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = _runtime_access(db, user, runtime_id, "member")
    ws = db.get(CodeWorkspace, rt.workspace_id)
    result = create_snapshot(Path(ws.root_path), Path(rt.runtime_root) / "snapshots")
    existing = db.scalar(select(ProjectRuntimeSnapshot).where(ProjectRuntimeSnapshot.runtime_id == rt.id, ProjectRuntimeSnapshot.manifest_sha256 == result["manifest_sha256"]))
    if existing:
        return existing
    snap = ProjectRuntimeSnapshot(runtime_id=rt.id, created_by=user.id, archive_path=result["archive_path"], manifest_sha256=result["manifest_sha256"],
                                  file_count=result["manifest"]["file_count"], total_bytes=result["manifest"]["total_bytes"], manifest=result["manifest"])
    db.add(snap); db.commit(); db.refresh(snap)
    return snap


@router.get("/{runtime_id}/snapshots", response_model=list[RuntimeSnapshotRead])
def list_snapshots(runtime_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = _runtime_access(db, user, runtime_id)
    return list(db.scalars(select(ProjectRuntimeSnapshot).where(ProjectRuntimeSnapshot.runtime_id == rt.id).order_by(ProjectRuntimeSnapshot.created_at.desc())).all())


@router.put("/{runtime_id}/secrets", response_model=RuntimeSecretRead)
def put_secret(runtime_id: str, payload: RuntimeSecretWrite, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = _runtime_access(db, user, runtime_id, "manager")
    try:
        ciphertext = encrypt_secret(payload.value, request.app.state.settings.project_runtime_secret_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    row = db.scalar(select(ProjectRuntimeSecret).where(ProjectRuntimeSecret.runtime_id == rt.id, ProjectRuntimeSecret.name == payload.name))
    if row is None:
        row = ProjectRuntimeSecret(runtime_id=rt.id, name=payload.name, ciphertext=ciphertext, created_by=user.id)
        db.add(row)
    else:
        row.ciphertext = ciphertext; row.updated_at = utcnow()
    db.commit(); db.refresh(row)
    return row


@router.get("/{runtime_id}/secrets", response_model=list[RuntimeSecretRead])
def list_secrets(runtime_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = _runtime_access(db, user, runtime_id, "manager")
    return list(db.scalars(select(ProjectRuntimeSecret).where(ProjectRuntimeSecret.runtime_id == rt.id).order_by(ProjectRuntimeSecret.name)).all())
