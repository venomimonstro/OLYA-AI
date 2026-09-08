from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import Field

from app.services.code_workspace import repo_map, resolve_inside, safe_relative_path, sha256_file, write_text
from app.services.git_collaboration import git_diff, git_status
from app.services.tool_reliability import StrictToolArgs, ToolRegistry, ToolSpec, ToolValidationError


class WorkspaceMapArgs(StrictToolArgs):
    max_files: int = Field(default=250, ge=1, le=500)


class WorkspaceReadArgs(StrictToolArgs):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1, le=1_000_000)
    end_line: int | None = Field(default=None, ge=1, le=1_000_000)


class WorkspaceWriteArgs(StrictToolArgs):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=500_000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class GitStatusArgs(StrictToolArgs):
    include_diff: bool = False
    staged: bool = False


class ToolScopeError(ToolValidationError):
    """Deterministic pre-side-effect scope rejection."""


def _require_allowed(path: str, allowed_paths: tuple[str, ...]) -> str:
    rel = safe_relative_path(path).as_posix()
    if not allowed_paths:
        return rel
    for allowed in allowed_paths:
        base = safe_relative_path(allowed).as_posix().rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            return rel
    raise ToolScopeError("Path is outside the agent's approved scope")


def build_coding_tool_registry(
    root: Path,
    *,
    allowed_paths: list[str] | tuple[str, ...] = (),
    before_write: Callable[[str, Path], None] | None = None,
    after_write: Callable[[dict], None] | None = None,
) -> ToolRegistry:
    """Minimal scoped coding tools. No shell, network, commit, push or secrets."""
    workspace = root.resolve()
    scope = tuple(safe_relative_path(item).as_posix() for item in allowed_paths)
    registry = ToolRegistry()

    def workspace_map(args: WorkspaceMapArgs) -> dict:
        result = repo_map(workspace, max_files=args.max_files)
        if not scope:
            return result
        files = [item for item in result.get("files", []) if any(item.get("path") == base or str(item.get("path", "")).startswith(base.rstrip("/") + "/") for base in scope)]
        return {"files": files, "file_count": len(files), "total_bytes": sum(int(item.get("bytes", 0)) for item in files), "scope": list(scope)}

    def workspace_read(args: WorkspaceReadArgs) -> dict:
        rel = _require_allowed(args.path, scope); target = resolve_inside(workspace, rel)
        if target.is_symlink() or not target.is_file(): raise ToolScopeError("Requested path is not a regular file")
        if target.stat().st_size > 2_000_000: raise ToolScopeError("File is too large for direct tool read")
        text = target.read_text(encoding="utf-8", errors="replace"); lines = text.splitlines(); start = min(args.start_line, len(lines) + 1); requested_end = args.end_line if args.end_line is not None else start + 399; end = min(max(start, requested_end), min(len(lines), start + 999)); selected = lines[start - 1:end]
        return {"path": rel, "start_line": start, "end_line": end, "total_lines": len(lines), "sha256": sha256_file(target), "content": "\n".join(selected)}

    def workspace_write(args: WorkspaceWriteArgs) -> dict:
        rel = _require_allowed(args.path, scope); target = resolve_inside(workspace, rel)
        if before_write is not None: before_write(rel, target)
        result = write_text(workspace, rel, args.content, expected_sha256=args.expected_sha256)
        if after_write is not None: after_write(dict(result))
        return result

    def status(args: GitStatusArgs) -> dict:
        result = git_status(workspace)
        if args.include_diff: result["diff"] = git_diff(workspace, staged=args.staged)
        return result

    registry.register(ToolSpec(name="workspace.map", description="List files visible inside the approved coding workspace scope.", args_model=WorkspaceMapArgs, handler=workspace_map, effect="read", timeout_seconds=8, max_retries=1, result_max_chars=30_000))
    registry.register(ToolSpec(name="workspace.read", description="Read a bounded line range from one approved workspace text file.", args_model=WorkspaceReadArgs, handler=workspace_read, effect="read", timeout_seconds=8, max_retries=1, result_max_chars=30_000))
    registry.register(ToolSpec(name="workspace.write", description="Atomically write one approved workspace file. expected_sha256 provides optimistic concurrency protection.", args_model=WorkspaceWriteArgs, handler=workspace_write, effect="write", timeout_seconds=10, max_retries=0, result_max_chars=8_000))
    registry.register(ToolSpec(name="git.status", description="Inspect local Git status and optionally a bounded diff. This tool never commits or pushes.", args_model=GitStatusArgs, handler=status, effect="read", timeout_seconds=15, max_retries=1, result_max_chars=40_000))
    return registry