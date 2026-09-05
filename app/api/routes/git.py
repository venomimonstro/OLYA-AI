from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CodeWorkspace, GitOperation, GitRepositoryBinding, ProjectRuntime, ProjectRuntimeSecret, User, utcnow
from app.schemas.git import GitBindingCreate, GitBindingRead, GitCommitRequest, GitExternalAction, GitOperationRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.code_workspace import WorkspaceError, repo_map
from app.services.git_collaboration import GitError, changed_paths, checkout_branch, clone_into_workspace, commit as git_commit, ensure_local_repo, fetch as git_fetch, git_diff, git_status, head, normalize_github_url, push as git_push, push_candidate_paths, remote_head, scan_secrets, set_origin
from app.services.project_runtime import RuntimeError, decrypt_secret

router = APIRouter(prefix="/v1/git", tags=["git"])


def _binding_access(db: Session, user: User, binding_id: str, minimum: str = "viewer") -> GitRepositoryBinding:
    row = db.get(GitRepositoryBinding, binding_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Git binding not found")
    require_project_role(db, user, row.project_id, minimum)
    return row


def _workspace(db: Session, binding: GitRepositoryBinding) -> CodeWorkspace:
    ws = db.get(CodeWorkspace, binding.workspace_id)
    if ws is None:
        raise HTTPException(status_code=409, detail="Bound workspace no longer exists")
    return ws


def _token(db: Session, request: Request, binding: GitRepositoryBinding) -> str:
    if binding.provider != "github":
        return ""
    if not binding.credential_secret_name:
        raise HTTPException(status_code=409, detail="GitHub credential secret is not configured")
    secret = db.scalar(select(ProjectRuntimeSecret).where(ProjectRuntimeSecret.runtime_id == binding.runtime_id, ProjectRuntimeSecret.name == binding.credential_secret_name))
    if secret is None:
        raise HTTPException(status_code=409, detail="Configured GitHub credential secret does not exist")
    try:
        return decrypt_secret(secret.ciphertext, request.app.state.settings.project_runtime_secret_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Project runtime secret key is unavailable") from exc


def _operation(db: Session, binding: GitRepositoryBinding, user: User, kind: str) -> GitOperation:
    branch = binding.working_branch if binding.mode == "branch" else binding.default_branch
    row = GitOperation(binding_id=binding.id, project_id=binding.project_id, created_by=user.id, kind=kind, status="running", branch=branch)
    db.add(row)
    db.flush()
    return row


def _finish(db: Session, op: GitOperation, *, status_: str, summary: dict | None = None, failure: str = "") -> GitOperation:
    op.status = status_
    op.summary = summary or {}
    op.failure_reason = failure[:4000]
    op.completed_at = utcnow()
    db.commit(); db.refresh(op)
    return op


@router.post("/bindings", response_model=GitBindingRead, status_code=status.HTTP_201_CREATED)
def create_binding(payload: GitBindingCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = db.get(ProjectRuntime, payload.runtime_id)
    if rt is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    require_project_role(db, user, rt.project_id, "manager")
    existing = db.scalar(select(GitRepositoryBinding).where(GitRepositoryBinding.runtime_id == rt.id))
    provider = payload.provider
    repo_url = owner = name = ""
    if provider == "github":
        if not payload.repository_url:
            raise HTTPException(status_code=422, detail="GitHub repository URL is required")
        try:
            repo_url, owner, name = normalize_github_url(payload.repository_url)
        except GitError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.mode == "local_only":
            raise HTTPException(status_code=422, detail="GitHub binding requires direct or branch mode")
    elif payload.repository_url or payload.credential_secret_name or payload.push_enabled:
        raise HTTPException(status_code=422, detail="Local-only Git binding cannot configure a remote or push")
    working_branch = payload.working_branch.strip()
    if payload.mode == "branch" and not working_branch:
        working_branch = f"x1/{rt.project_id[:8]}"
    if payload.mode != "branch" and working_branch:
        raise HTTPException(status_code=422, detail="working_branch is only valid in branch mode")
    if existing is not None:
        if existing.provider != "local" or provider == "local":
            raise HTTPException(status_code=409, detail="Runtime already has a Git binding")
        existing.provider = provider
        existing.repository_url = repo_url
        existing.repository_owner = owner
        existing.repository_name = name
        existing.default_branch = payload.default_branch
        existing.working_branch = working_branch
        existing.mode = payload.mode
        existing.push_enabled = payload.push_enabled
        existing.credential_secret_name = payload.credential_secret_name
        existing.status = "configured"
        db.commit(); db.refresh(existing)
        return existing
    row = GitRepositoryBinding(project_id=rt.project_id, runtime_id=rt.id, workspace_id=rt.workspace_id, created_by=user.id,
                               provider=provider, repository_url=repo_url, repository_owner=owner, repository_name=name,
                               default_branch=payload.default_branch, working_branch=working_branch, mode=payload.mode, push_enabled=payload.push_enabled,
                               credential_secret_name=payload.credential_secret_name)
    db.add(row); db.commit(); db.refresh(row)
    return row



@router.get("/bindings/runtime/{runtime_id}", response_model=GitBindingRead)
def get_runtime_binding(runtime_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rt = db.get(ProjectRuntime, runtime_id)
    if rt is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    require_project_role(db, user, rt.project_id, "viewer")
    row = db.scalar(select(GitRepositoryBinding).where(GitRepositoryBinding.runtime_id == rt.id))
    if row is None:
        raise HTTPException(status_code=404, detail="Git binding not found")
    return row

@router.get("/bindings/{binding_id}", response_model=GitBindingRead)
def get_binding(binding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _binding_access(db, user, binding_id)


@router.post("/bindings/{binding_id}/init", response_model=GitOperationRead, status_code=status.HTTP_201_CREATED)
def init_repo(binding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "member")
    ws = _workspace(db, binding); op = _operation(db, binding, user, "init")
    try:
        result = ensure_local_repo(Path(ws.root_path), binding.default_branch)
        if binding.provider == "github":
            set_origin(Path(ws.root_path), binding.repository_url)
        if binding.mode == "branch":
            checkout_branch(Path(ws.root_path), binding.working_branch, base_branch=binding.default_branch)
        binding.last_local_head = result.get("head", "")
        binding.status = "ready"
        db.flush()
        return _finish(db, op, status_="completed", summary=result)
    except GitError as exc:
        db.rollback(); op = _operation(db, binding, user, "init")
        return _finish(db, op, status_="failed", failure=str(exc))


@router.post("/bindings/{binding_id}/clone", response_model=GitOperationRead, status_code=status.HTTP_201_CREATED)
def clone_repo(binding_id: str, payload: GitExternalAction, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "manager")
    if binding.provider != "github":
        raise HTTPException(status_code=409, detail="Clone requires a GitHub binding")
    if not payload.confirm_external_action:
        raise HTTPException(status_code=409, detail="External GitHub read must be explicitly confirmed")
    ws = _workspace(db, binding); token = _token(db, request, binding); op = _operation(db, binding, user, "clone")
    try:
        result = clone_into_workspace(Path(ws.root_path), binding.repository_url, binding.default_branch, token)
        if binding.mode == "branch":
            checkout_branch(Path(ws.root_path), binding.working_branch, base_branch=binding.default_branch)
            result = git_status(Path(ws.root_path))
        binding.last_local_head = result.get("head", ""); binding.status = "ready"
        mapping = repo_map(Path(ws.root_path)); ws.file_count = mapping["file_count"]; ws.total_bytes = mapping["total_bytes"]; ws.updated_at = utcnow()
        db.flush()
        return _finish(db, op, status_="completed", summary=result)
    except GitError as exc:
        return _finish(db, op, status_="failed", failure=str(exc))


@router.get("/bindings/{binding_id}/status")
def status_repo(binding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id); ws = _workspace(db, binding)
    try:
        return git_status(Path(ws.root_path))
    except GitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/bindings/{binding_id}/diff")
def diff_repo(binding_id: str, staged: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id); ws = _workspace(db, binding)
    try:
        return git_diff(Path(ws.root_path), staged=staged)
    except GitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/bindings/{binding_id}/secret-scan")
def secret_scan(binding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "member"); ws = _workspace(db, binding)
    root = Path(ws.root_path)
    return {"findings": scan_secrets(root, changed_paths(root))}


@router.post("/bindings/{binding_id}/commit", response_model=GitOperationRead, status_code=status.HTTP_201_CREATED)
def commit_repo(binding_id: str, payload: GitCommitRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "member"); ws = _workspace(db, binding); op = _operation(db, binding, user, "commit")
    try:
        result = git_commit(Path(ws.root_path), payload.message.strip(), payload.paths, expected_head=payload.expected_head)
        op.head_before = result["head_before"]; op.head_after = result["head_after"]
        binding.last_local_head = result["head_after"]; db.flush()
        return _finish(db, op, status_="completed", summary=result)
    except (GitError, WorkspaceError) as exc:
        return _finish(db, op, status_="failed", failure=str(exc))


@router.post("/bindings/{binding_id}/fetch", response_model=GitOperationRead, status_code=status.HTTP_201_CREATED)
def fetch_repo(binding_id: str, payload: GitExternalAction, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "member")
    if binding.provider != "github":
        raise HTTPException(status_code=409, detail="Fetch requires a GitHub binding")
    if not payload.confirm_external_action:
        raise HTTPException(status_code=409, detail="External GitHub read must be explicitly confirmed")
    ws = _workspace(db, binding); token = _token(db, request, binding); op = _operation(db, binding, user, "fetch")
    try:
        root = Path(ws.root_path)
        set_origin(root, binding.repository_url)
        target_branch = binding.default_branch
        if binding.mode == "branch":
            try:
                if remote_head(root, binding.working_branch, token):
                    target_branch = binding.working_branch
            except GitError:
                target_branch = binding.default_branch
        result = git_fetch(root, target_branch, token)
        binding.last_remote_head = result["remote_head_after"]; binding.last_local_head = result["local_head"]
        op.remote_head = result["remote_head_after"]; db.flush()
        return _finish(db, op, status_="completed", summary=result)
    except GitError as exc:
        return _finish(db, op, status_="failed", failure=str(exc))


@router.post("/bindings/{binding_id}/push", response_model=GitOperationRead, status_code=status.HTTP_201_CREATED)
def push_repo(binding_id: str, payload: GitExternalAction, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id, "manager")
    if binding.provider != "github" or not binding.push_enabled:
        raise HTTPException(status_code=403, detail="GitHub push capability is disabled for this project")
    if not payload.confirm_external_action:
        raise HTTPException(status_code=409, detail="External GitHub write must be explicitly confirmed")
    ws = _workspace(db, binding); root = Path(ws.root_path)
    token = _token(db, request, binding)
    target_branch = binding.working_branch if binding.mode == "branch" else binding.default_branch
    try:
        set_origin(root, binding.repository_url)
        remote_before, candidate_paths = push_candidate_paths(root, target_branch, token)
    except GitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    findings = scan_secrets(root, sorted(set(changed_paths(root)) | set(candidate_paths)))
    if findings:
        raise HTTPException(status_code=409, detail={"message": "Secret scan blocked push", "findings": findings})
    if payload.expected_remote_head and remote_before != payload.expected_remote_head:
        raise HTTPException(status_code=409, detail="Remote branch changed since push was planned")
    op = _operation(db, binding, user, "push")
    try:
        target_branch = binding.working_branch if binding.mode == "branch" else binding.default_branch
        if binding.mode == "branch" and git_status(root).get("branch") != target_branch:
            raise HTTPException(status_code=409, detail="Workspace is not on the configured working branch")
        before = head(root); result = git_push(root, target_branch, token, expected_head=payload.expected_head, expected_remote_head=payload.expected_remote_head)
        op.head_before = before; op.head_after = result["local_head"]; op.remote_head = result["remote_head_after"]
        binding.last_local_head = result["local_head"]; binding.last_remote_head = result["remote_head_after"]; db.flush()
        return _finish(db, op, status_="completed", summary=result)
    except GitError as exc:
        return _finish(db, op, status_="failed", failure=str(exc))


@router.get("/bindings/{binding_id}/operations", response_model=list[GitOperationRead])
def list_operations(binding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    binding = _binding_access(db, user, binding_id)
    return list(db.scalars(select(GitOperation).where(GitOperation.binding_id == binding.id).order_by(GitOperation.created_at.desc()).limit(100)).all())
