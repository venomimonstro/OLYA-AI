#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    contract = (ROOT / "app/services/agent_contract.py").read_text("utf-8")
    autonomous = (ROOT / "app/services/autonomous_development.py").read_text("utf-8")
    dev_chat = (ROOT / "app/api/routes/development_chat.py").read_text("utf-8")
    tasks = (ROOT / "app/services/tasks.py").read_text("utf-8")
    execution = (ROOT / "app/services/engineering_execution.py").read_text("utf-8")
    tools = (ROOT / "app/services/tool_reliability.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required = {
        "canonical_completion": (contract, "agent_completion_blockers"),
        "plan_not_enough": (contract, 'if plan.status != "completed"'),
        "task_completion": (contract, 'task.status != "completed"'),
        "task_evidence_gate": (contract, "completion_blockers(db, task)"),
        "execution_verified": (contract, 'execution.status != "verified"'),
        "deadline_progress": (contract, 'checkpoint(stage)'),
        "immutable_contract": (autonomous, "contract_sha256"),
        "contract_drift_blocks": (autonomous, 'state["contract"]["drift_detected"]'),
        "completion_gate_state": (autonomous, 'state["completion_gate"]'),
        "completion_proof": (autonomous, 'canonical_tasks_verified_evidence_and_execution_state'),
        "subagent_total_budget": (autonomous, "ledger.subagent_calls_used >= ledger.subagent_budget"),
        "subagent_parallel_budget": (autonomous, "ledger.active_subagents >= ledger.max_parallel_subagents"),
        "terminal_ledger_guard": (autonomous, 'ledger.status in {"blocked", "completed"}'),
        "resume_checkpoint": (autonomous, "resume_from_checkpoint"),
        "stale_slot_recovery": (autonomous, "recover_abandoned_slots"),
        "continue_deadline_gate": (dev_chat, 'require_agent_progress_budget("development next step")'),
        "pause": (dev_chat, 'command == "pause"'),
        "resume": (dev_chat, 'command == "resume"'),
        "rollback": (dev_chat, 'command == "rollback"'),
        "verified_evidence": (tasks, 'evidence.state != "verified"'),
        "completion_blocked": (tasks, "CompletionBlockedError"),
        "snapshot_before_patch": (execution, "ensure_snapshot"),
        "approved_scope": (execution, "_approved_scope"),
        "rollback_files": (execution, "rollback_changed_files"),
        "tool_call_budget": (tools, "self.calls_used>=self.max_calls"),
        "tool_loop_guard": (tools, "ToolLoopError"),
        "write_no_retry": (tools, 'self.effect == "write" and self.max_retries'),
        "uncertain_write_replay_block": (tools, "_write_state_uncertain"),
    }
    for code, (source, token) in required.items():
        if token not in source:
            errors.append({"code": code, "token": token})

    if '("scripts.agent_mvp_lock_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_agent_mvp_lock_audit"})

    return {
        "format": "x1-agent-mvp-lock-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
