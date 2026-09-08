from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from app.inference.client import LlamaClient
from app.services.code_workspace import resolve_inside, safe_relative_path
from app.services.coding_tools import build_coding_tool_registry
from app.services.sandbox import SandboxError, run_in_container
from app.services.tool_agent import ToolAgentOutcome, run_tool_agent
from app.services.tool_reliability import StrictToolArgs, ToolSession, ToolSpec


class VerificationRunArgs(StrictToolArgs):
    check: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")


@dataclass(frozen=True)
class VerificationEvidence:
    check: str
    argv: tuple[str, ...]
    passed: bool
    exit_code: int | None
    timed_out: bool
    sandbox_level: str
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class CodingProof:
    done: bool
    reason: str
    checkpoint_id: str
    changed_paths: tuple[str, ...]
    diff_lines: int
    required_checks: tuple[str, ...]
    passed_checks: tuple[str, ...]
    evidence: tuple[VerificationEvidence, ...]
    rolled_back: bool


@dataclass(frozen=True)
class CodingAgentResult:
    text: str
    outcome: ToolAgentOutcome
    proof: CodingProof


@dataclass
class _Checkpoint:
    checkpoint_id: str = field(default_factory=lambda: f"cp_{uuid4().hex}")
    original: dict[str, bytes | None] = field(default_factory=dict)
    touched: list[str] = field(default_factory=list)
    total_snapshot_bytes: int = 0
    max_snapshot_bytes: int = 12 * 1024 * 1024

    def capture(self, relative: str, target: Path) -> None:
        if relative in self.original:
            return
        data = target.read_bytes() if target.is_file() else None
        size = len(data) if data is not None else 0
        if self.total_snapshot_bytes + size > self.max_snapshot_bytes:
            raise RuntimeError("Coding checkpoint exceeds snapshot budget")
        self.original[relative] = data
        self.total_snapshot_bytes += size

    def mark_touched(self, result: dict[str, Any]) -> None:
        path = str(result.get("path") or "")
        if path and path not in self.touched:
            self.touched.append(path)

    def rollback(self, root: Path) -> None:
        for relative, original in reversed(list(self.original.items())):
            target = resolve_inside(root, relative)
            if original is None:
                if target.exists() and target.is_file():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".x1rollback")
            temporary.write_bytes(original)
            os.replace(temporary, target)


def _diff_line_count(root: Path, checkpoint: _Checkpoint) -> int:
    total = 0
    for relative, before in checkpoint.original.items():
        target = resolve_inside(root, relative)
        after = target.read_bytes() if target.is_file() else b""
        before_text = (before or b"").decode("utf-8", errors="replace").splitlines()
        after_text = after.decode("utf-8", errors="replace").splitlines()
        for line in difflib.unified_diff(before_text, after_text, lineterm=""):
            if line.startswith(("+++", "---", "@@")):
                continue
            if line.startswith(("+", "-")):
                total += 1
    return total


def _latest_passed(evidence: list[VerificationEvidence], check: str) -> bool:
    rows = [item for item in evidence if item.check == check]
    return bool(rows and rows[-1].passed)


def _build_registry(
    *,
    root: Path,
    scratch: Path,
    allowed_paths: tuple[str, ...],
    checks: dict[str, tuple[str, ...]],
    checkpoint: _Checkpoint,
    evidence: list[VerificationEvidence],
    preferred_backend: str,
    image: str,
    timeout_seconds: int,
    cpu_limit: float,
    memory_mb: int,
    process_limit: int,
):
    registry = build_coding_tool_registry(
        root,
        allowed_paths=allowed_paths,
        before_write=checkpoint.capture,
        after_write=checkpoint.mark_touched,
    )

    def verification_run(args: VerificationRunArgs) -> dict[str, Any]:
        argv = checks.get(args.check)
        if argv is None:
            raise ValueError("Verification check is not approved by the server")
        try:
            result = run_in_container(
                preferred_backend=preferred_backend,
                image=image,
                workspace=root,
                scratch=scratch,
                argv=list(argv),
                timeout_seconds=timeout_seconds,
                cpu_limit=cpu_limit,
                memory_mb=memory_mb,
                process_limit=process_limit,
                network_policy="deny",
                env={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                    "HOME": "/x1-runtime",
                },
                read_only_workspace=True,
            )
        except SandboxError as exc:
            raise RuntimeError("Sandbox verification unavailable") from exc
        passed = bool(result.get("exit_code") == 0 and not result.get("timed_out"))
        row = VerificationEvidence(
            check=args.check,
            argv=tuple(argv),
            passed=passed,
            exit_code=result.get("exit_code"),
            timed_out=bool(result.get("timed_out")),
            sandbox_level=str(result.get("sandbox_level") or "unknown"),
            stdout_tail=str(result.get("stdout") or "")[-4000:],
            stderr_tail=str(result.get("stderr") or "")[-4000:],
        )
        evidence.append(row)
        return {
            "check": row.check,
            "passed": row.passed,
            "exit_code": row.exit_code,
            "timed_out": row.timed_out,
            "sandbox_level": row.sandbox_level,
            "stdout": row.stdout_tail,
            "stderr": row.stderr_tail,
        }

    registry.register(
        ToolSpec(
            name="verification.run",
            description="Run one server-approved verification check in the isolated read-only sandbox. The check name must come from the task contract.",
            args_model=VerificationRunArgs,
            handler=verification_run,
            effect="read",
            timeout_seconds=min(300, max(5, timeout_seconds + 15)),
            max_retries=0,
            result_max_chars=12_000,
            cacheable=False,
        )
    )
    return registry


async def run_coding_agent(
    llama: LlamaClient,
    *,
    goal: str,
    root: Path,
    scratch: Path,
    allowed_paths: list[str] | tuple[str, ...],
    checks: dict[str, list[str] | tuple[str, ...]],
    required_checks: list[str] | tuple[str, ...],
    preferred_backend: str,
    image: str,
    timeout_seconds: int = 180,
    cpu_limit: float = 1.0,
    memory_mb: int = 1024,
    process_limit: int = 64,
    max_changed_files: int = 12,
    max_diff_lines: int = 800,
    max_steps: int = 12,
    rollback_on_failure: bool = True,
) -> CodingAgentResult:
    """Execute a bounded coding task and return server-owned proof of result.

    Completion is computed from real sandbox evidence and diff policy; model prose
    is never accepted as proof that tests passed.
    """
    workspace = root.resolve(); runtime_scratch = scratch.resolve(); runtime_scratch.mkdir(parents=True, exist_ok=True)
    scope = tuple(safe_relative_path(item).as_posix() for item in allowed_paths)
    approved_checks = {str(name): tuple(str(x) for x in argv) for name, argv in checks.items()}
    required = tuple(dict.fromkeys(str(item) for item in required_checks))
    if not goal.strip():
        raise ValueError("Coding agent goal is empty")
    if not scope:
        raise ValueError("Coding agent requires an explicit approved path scope")
    if not required or any(name not in approved_checks for name in required):
        raise ValueError("Every required verification check must be server-approved")

    checkpoint = _Checkpoint()
    evidence: list[VerificationEvidence] = []
    registry = _build_registry(
        root=workspace,
        scratch=runtime_scratch,
        allowed_paths=scope,
        checks=approved_checks,
        checkpoint=checkpoint,
        evidence=evidence,
        preferred_backend=preferred_backend,
        image=image,
        timeout_seconds=timeout_seconds,
        cpu_limit=cpu_limit,
        memory_mb=memory_mb,
        process_limit=process_limit,
    )
    session = ToolSession(registry, max_calls=32, max_same_call=4, max_consecutive_same_tool=6, history_size=40)
    check_lines = "\n".join(f"- {name}: {list(approved_checks[name])}" for name in approved_checks)
    messages = [
        {
            "role": "system",
            "content": (
                "You are the OLYA AI Coding Agent. Work only inside APPROVED PATHS. "
                "Use selective reads, make the smallest necessary patch, inspect status/diff, and run the required server-approved verification checks. "
                "If a check fails, inspect the failure and repair within scope, then rerun it. "
                "Never claim tests ran unless verification.run returned the evidence in this tool session. "
                "Do not ask for shell, network, commit, push, secrets, or files outside scope. "
                "Only provide a final answer after the required checks pass; otherwise explain the blocker without claiming completion."
            ),
        },
        {
            "role": "user",
            "content": (
                f"GOAL:\n{goal.strip()}\n\nAPPROVED PATHS:\n" + "\n".join(f"- {item}" for item in scope) +
                f"\n\nDIFF BUDGET: max {max_changed_files} changed files, max {max_diff_lines} changed lines."
                "\n\nSERVER-APPROVED CHECKS:\n" + check_lines +
                "\n\nREQUIRED BEFORE DONE:\n" + "\n".join(f"- {item}" for item in required)
            ),
        },
    ]
    outcome = await run_tool_agent(llama, messages=messages, session=session, max_steps=max_steps, max_tool_errors=2, final_max_tokens=1200)

    changed = tuple(checkpoint.touched)
    diff_lines = _diff_line_count(workspace, checkpoint)
    checks_ok = all(_latest_passed(evidence, name) for name in required)
    within_budget = len(changed) <= max(1, int(max_changed_files)) and diff_lines <= max(1, int(max_diff_lines))
    agent_finished = outcome.stopped_reason == "final_answer"
    done = bool(agent_finished and changed and checks_ok and within_budget)
    if not changed:
        reason = "no_patch_produced"
    elif not within_budget:
        reason = "diff_budget_exceeded"
    elif not checks_ok:
        reason = "required_verification_missing_or_failed"
    elif not agent_finished:
        reason = outcome.stopped_reason
    else:
        reason = "verified"

    rolled_back = False
    if not done and rollback_on_failure and checkpoint.original:
        checkpoint.rollback(workspace); rolled_back = True

    proof = CodingProof(
        done=done,
        reason=reason,
        checkpoint_id=checkpoint.checkpoint_id,
        changed_paths=changed,
        diff_lines=diff_lines,
        required_checks=required,
        passed_checks=tuple(name for name in required if _latest_passed(evidence, name)),
        evidence=tuple(evidence),
        rolled_back=rolled_back,
    )
    final_text = outcome.text if done else "Coding task was not marked done because server-owned proof requirements were not satisfied."
    return CodingAgentResult(text=final_text, outcome=outcome, proof=proof)
