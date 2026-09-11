from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CodeWorkspace, EngineeringExecution, EngineeringExecutionEvent, EngineeringRoleTurn,
    EngineeringRun, ProjectRuntime, ProjectRuntimeSnapshot, Task, utcnow,
)
from app.schemas.chat import ChatMessage
from app.schemas.engineering import ImplementationPatchOutput
from app.services.sandbox import SandboxError, run_in_container, sandbox_capabilities
from app.services.code_workspace import WorkspaceError, path_allowed, resolve_inside, run_command, safe_relative_path, sha256_file, write_text
from app.services.project_runtime import create_snapshot


class ExecutionError(ValueError):
    pass


def serialize_execution(db: Session, row: EngineeringExecution) -> dict:
    events = list(db.scalars(select(EngineeringExecutionEvent).where(
        EngineeringExecutionEvent.execution_id == row.id
    ).order_by(EngineeringExecutionEvent.sequence)).all())
    return {
        "id": row.id, "engineering_run_id": row.engineering_run_id, "project_id": row.project_id,
        "task_id": row.task_id, "runtime_id": row.runtime_id, "workspace_id": row.workspace_id,
        "created_by": row.created_by, "status": row.status, "state_version": row.state_version,
        "snapshot_id": row.snapshot_id, "attempt": row.attempt, "max_repairs": row.max_repairs,
        "change_manifest": row.change_manifest or [], "verification_results": row.verification_results or [],
        "failure_reason": row.failure_reason or "", "events": events, "started_at": row.started_at,
        "completed_at": row.completed_at, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _turn_output(db: Session, run_id: str, role: str) -> dict:
    row = db.scalar(select(EngineeringRoleTurn).where(
        EngineeringRoleTurn.run_id == run_id, EngineeringRoleTurn.role == role
    ).order_by(EngineeringRoleTurn.sequence.desc()))
    return dict(row.output or {}) if row else {}


def create_execution(db: Session, *, user_id: str, run: EngineeringRun, max_repairs: int) -> EngineeringExecution:
    if run.status != "approved":
        raise ExecutionError("Engineering run must be approved before execution")
    if not run.runtime_id or not run.workspace_id:
        raise ExecutionError("Engineering run requires an isolated project runtime")
    existing = db.scalar(select(EngineeringExecution).where(EngineeringExecution.engineering_run_id == run.id))
    if existing:
        return existing
    runtime = db.get(ProjectRuntime, run.runtime_id)
    workspace = db.get(CodeWorkspace, run.workspace_id)
    if runtime is None or workspace is None:
        raise ExecutionError("Runtime or workspace not found")
    row = EngineeringExecution(
        engineering_run_id=run.id, project_id=run.project_id, task_id=run.task_id,
        runtime_id=runtime.id, workspace_id=workspace.id, created_by=user_id,
        status="planned", max_repairs=max_repairs,
    )
    db.add(row); db.flush()
    add_event(db, row, "created", "ok", {"engineering_run_id": run.id})
    return row


def add_event(db: Session, row: EngineeringExecution, kind: str, status: str, details: dict) -> EngineeringExecutionEvent:
    persisted = int(db.scalar(select(func.coalesce(func.max(EngineeringExecutionEvent.sequence), 0)).where(
        EngineeringExecutionEvent.execution_id == row.id
    )) or 0)
    pending = max((x.sequence for x in db.new if isinstance(x, EngineeringExecutionEvent) and x.execution_id == row.id), default=0)
    seq = max(persisted, pending) + 1
    event = EngineeringExecutionEvent(execution_id=row.id, sequence=seq, kind=kind, status=status, details=details)
    db.add(event)
    return event


def ensure_snapshot(db: Session, row: EngineeringExecution, user_id: str) -> ProjectRuntimeSnapshot:
    if row.snapshot_id:
        snap = db.get(ProjectRuntimeSnapshot, row.snapshot_id)
        if snap:
            return snap
    runtime = db.get(ProjectRuntime, row.runtime_id)
    workspace = db.get(CodeWorkspace, row.workspace_id)
    if runtime is None or workspace is None:
        raise ExecutionError("Runtime or workspace not found")
    result = create_snapshot(Path(workspace.root_path), Path(runtime.runtime_root) / "snapshots")
    snap = db.scalar(select(ProjectRuntimeSnapshot).where(
        ProjectRuntimeSnapshot.runtime_id == runtime.id,
        ProjectRuntimeSnapshot.manifest_sha256 == result["manifest_sha256"],
    ))
    if snap is None:
        snap = ProjectRuntimeSnapshot(
            runtime_id=runtime.id, created_by=user_id, archive_path=result["archive_path"],
            manifest_sha256=result["manifest_sha256"], file_count=result["manifest"]["file_count"],
            total_bytes=result["manifest"]["total_bytes"], manifest=result["manifest"],
        )
        db.add(snap); db.flush()
    row.snapshot_id = snap.id
    add_event(db, row, "snapshot", "ok", {"snapshot_id": snap.id, "manifest_sha256": snap.manifest_sha256})
    return snap


def _approved_scope(db: Session, run: EngineeringRun) -> tuple[list[str], list[str], list[list[str]]]:
    coordinator = _turn_output(db, run.id, "coordinator")
    developer = _turn_output(db, run.id, "developer")
    scope = [safe_relative_path(x).as_posix() for x in coordinator.get("scope_paths", [])]
    files = [safe_relative_path(x).as_posix() for x in developer.get("files_to_change", [])]
    for path in files:
        if scope and not path_allowed(path, scope):
            raise ExecutionError(f"Developer file outside approved scope: {path}")
    commands = developer.get("verification_commands", []) or []
    return scope, files, commands


def build_patch_messages(db: Session, row: EngineeringExecution) -> tuple[list[ChatMessage], str]:
    run = db.get(EngineeringRun, row.engineering_run_id)
    workspace = db.get(CodeWorkspace, row.workspace_id)
    task = db.get(Task, row.task_id)
    if run is None or workspace is None or task is None:
        raise ExecutionError("Execution references missing state")
    scope, files, commands = _approved_scope(db, run)
    root = Path(workspace.root_path).resolve()
    file_payload = []
    total_chars = 0
    for rel in files[:40]:
        target = resolve_inside(root, rel)
        if target.exists():
            if not target.is_file() or target.stat().st_size > 350_000:
                raise ExecutionError(f"File is too large or not regular: {rel}")
            text = target.read_text("utf-8", errors="replace")
            total_chars += len(text)
            if total_chars > 1_000_000:
                raise ExecutionError("Approved file context exceeds execution limit")
            file_payload.append({"path": rel, "sha256": sha256_file(target), "content": text})
        else:
            file_payload.append({"path": rel, "sha256": None, "content": None})
    context = {
        "goal": task.goal,
        "approved_scope_paths": scope,
        "approved_files": files,
        "approved_verification_commands": commands,
        "developer_handoff": _turn_output(db, run.id, "developer"),
        "tester_handoff": _turn_output(db, run.id, "tester"),
        "reviewer_handoff": _turn_output(db, run.id, "reviewer"),
        "files": file_payload,
        "attempt": row.attempt + 1,
        "previous_verification_failures": row.verification_results or [],
    }
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    schema = ImplementationPatchOutput.model_json_schema()
    messages = [
        ChatMessage(role="system", content=(
            "You are the X1 implementation executor. Produce only the minimal file replacements needed for the approved task. "
            "You may only return paths from approved_files and MUST echo each file sha256 as expected_sha256 (null for a new file). "
            "Do not invent completed tests, permissions, shell access, network access, or extra files. "
            "Return one JSON object matching the schema."
        )),
        ChatMessage(role="user", content="APPROVED EXECUTION STATE:\n" + raw + "\n\nOUTPUT JSON SCHEMA:\n" + json.dumps(schema, ensure_ascii=False)),
    ]
    return messages, digest


def parse_patch(raw: str, *, approved_files: list[str], scope: list[str]) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ExecutionError("Implementation output is not JSON")
    try:
        parsed = ImplementationPatchOutput.model_validate(json.loads(raw[start:end + 1])).model_dump()
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExecutionError("Implementation output failed validation") from exc
    seen = set()
    for item in parsed["changes"]:
        path = safe_relative_path(item["path"]).as_posix()
        if path not in approved_files:
            raise ExecutionError(f"Patch path was not approved: {path}")
        if scope and not path_allowed(path, scope):
            raise ExecutionError(f"Patch path escapes approved scope: {path}")
        if path in seen:
            raise ExecutionError("Patch contains duplicate file path")
        seen.add(path); item["path"] = path
    return parsed


def apply_patch(db: Session, row: EngineeringExecution, patch: dict) -> list[dict]:
    workspace = db.get(CodeWorkspace, row.workspace_id)
    run = db.get(EngineeringRun, row.engineering_run_id)
    if workspace is None or run is None:
        raise ExecutionError("Workspace not found")
    scope, approved_files, _ = _approved_scope(db, run)
    root = Path(workspace.root_path).resolve()
    changes = []
    for item in patch["changes"]:
        path = item["path"]
        if path not in approved_files or (scope and not path_allowed(path, scope)):
            raise ExecutionError("Patch scope changed before application")
        target = resolve_inside(root, path)
        expected = item.get("expected_sha256")
        current = sha256_file(target) if target.is_file() else None
        if current != expected:
            raise ExecutionError(f"File changed after patch context was built: {path}")
        result = write_text(root, path, item["content"], expected_sha256=expected)
        changes.append(result)
    row.change_manifest = changes
    add_event(db, row, "patch_applied", "ok", {"changes": changes})
    return changes


def verification_commands(db: Session, row: EngineeringExecution, patch: dict) -> list[list[str]]:
    run = db.get(EngineeringRun, row.engineering_run_id)
    if run is None:
        raise ExecutionError("Engineering run not found")
    _, _, approved = _approved_scope(db, run)
    # Patch may repeat approved commands but cannot expand execution authority.
    proposed = patch.get("verification_commands", []) or []
    normalized_approved = {json.dumps(x, ensure_ascii=False) for x in approved}
    commands = list(approved)
    for cmd in proposed:
        if json.dumps(cmd, ensure_ascii=False) in normalized_approved and cmd not in commands:
            commands.append(cmd)
    return commands


def run_verification(db: Session, row: EngineeringExecution, commands: list[list[str]], *, timeout_seconds: int, allow_unsafe: bool, sandbox_settings=None) -> tuple[bool, list[dict]]:
    workspace = db.get(CodeWorkspace, row.workspace_id)
    runtime = db.get(ProjectRuntime, row.runtime_id)
    if workspace is None or runtime is None:
        raise ExecutionError("Runtime or workspace not found")
    root = Path(workspace.root_path).resolve()
    results = []
    for argv in commands:
        try:
            # Static checks may run directly. All broader project execution must use a real container sandbox.
            try:
                result = run_command(root, argv, timeout_seconds, allow_unsafe=False)
            except WorkspaceError as static_exc:
                if sandbox_settings is None:
                    raise static_exc
                caps = sandbox_capabilities(sandbox_settings.project_sandbox_backend, sandbox_settings.project_sandbox_image)
                if not caps["available"]:
                    raise WorkspaceError(caps["reason"] or "Container sandbox unavailable")
                scratch = Path(runtime.runtime_root).resolve() / "sandbox" / row.id
                result = run_in_container(
                    preferred_backend=sandbox_settings.project_sandbox_backend, image=sandbox_settings.project_sandbox_image,
                    workspace=root, scratch=scratch, argv=argv, timeout_seconds=timeout_seconds,
                    cpu_limit=runtime.cpu_limit, memory_mb=runtime.memory_limit_mb, process_limit=runtime.process_limit,
                    network_policy=runtime.network_policy, env=None,
                )
        except (WorkspaceError, SandboxError) as exc:
            result = {"argv": argv, "exit_code": None, "timed_out": False, "stdout": "", "stderr": str(exc), "sandbox_level": "blocked"}
        results.append(result)
        if result.get("timed_out") or result.get("exit_code") != 0:
            row.verification_results = results
            add_event(db, row, "verification", "failed", {"result": result})
            return False, results
    row.verification_results = results
    add_event(db, row, "verification", "ok", {"commands": len(results)})
    return True, results


def rollback_changed_files(db: Session, row: EngineeringExecution) -> None:
    if not row.snapshot_id:
        raise ExecutionError("Cannot rollback without snapshot")
    snap = db.get(ProjectRuntimeSnapshot, row.snapshot_id)
    workspace = db.get(CodeWorkspace, row.workspace_id)
    if snap is None or workspace is None:
        raise ExecutionError("Snapshot or workspace not found")
    root = Path(workspace.root_path).resolve()
    wanted = {safe_relative_path(x["path"]).as_posix() for x in (row.change_manifest or [])}
    existing_before = {x["path"] for x in (snap.manifest or {}).get("files", [])}
    restored = set()
    with tarfile.open(snap.archive_path, "r:gz") as tf:
        members = {m.name: m for m in tf.getmembers() if m.isfile()}
        for rel in wanted:
            target = resolve_inside(root, rel)
            if rel in existing_before:
                member = members.get(rel)
                if member is None:
                    raise ExecutionError(f"Snapshot is missing changed file: {rel}")
                src = tf.extractfile(member)
                if src is None:
                    raise ExecutionError("Snapshot extraction failed")
                data = src.read()
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".x1rollback")
                tmp.write_bytes(data); tmp.replace(target); restored.add(rel)
            else:
                target.unlink(missing_ok=True); restored.add(rel)
    add_event(db, row, "rollback", "ok", {"files": sorted(restored), "snapshot_id": snap.id})


def can_run_unsafe(runtime: ProjectRuntime, operator_enabled: bool) -> bool:
    # linux_namespace currently proves network/user namespace only, not filesystem jail.
    return bool(operator_enabled and runtime.isolation_backend in {"container", "filesystem_jail"})
