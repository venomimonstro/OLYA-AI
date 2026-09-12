#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    deadline = (ROOT / "app/services/deadline.py").read_text("utf-8")
    http = (ROOT / "app/services/http_limits.py").read_text("utf-8")
    auth = (ROOT / "app/services/auth.py").read_text("utf-8")
    api = (ROOT / "app/api/routes/api_client.py").read_text("utf-8")
    governor = (ROOT / "app/services/resource_governor.py").read_text("utf-8")
    llama = (ROOT / "app/inference/client.py").read_text("utf-8")
    research = (ROOT / "app/services/research.py").read_text("utf-8")
    workspace = (ROOT / "app/services/code_workspace.py").read_text("utf-8")
    sandbox = (ROOT / "app/services/sandbox.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required = {
        "monotonic_budget": (deadline, "deadline_at=now + budget"),
        "reuse_budget": (deadline, "if existing is not None and not replace"),
        "client_cannot_extend": (deadline, "budget = min(server_budget, requested)"),
        "http_root": (http, 'source="http"'),
        "http_force_new": (http, "replace=True"),
        "session_reuse": (auth, "begin_deadline_from_headers"),
        "api_reuse": (api, "begin_deadline_from_headers"),
        "queue_remaining": (governor, "request_remaining = remaining_seconds()"),
        "queue_deadline": (governor, "request deadline exceeded while waiting for inference"),
        "llama_stream": (llama, 'async with asyncio.timeout(budget)'),
        "llama_tool": (llama, 'stage="local tool inference"'),
        "research": (research, 'stage="research fetch"'),
        "workspace": (workspace, 'stage="workspace verification command"'),
        "sandbox_command": (sandbox, 'stage="sandbox command"'),
        "sandbox_remote": (sandbox, 'stage=f"sandbox remote {path}"'),
        "cleanup_after_deadline": (sandbox, "Cleanup is intentionally allowed after request deadline expiry"),
    }
    for code, (source, token) in required.items():
        if token not in source:
            errors.append({"code": code, "token": token})

    if "monotonic" not in deadline:
        errors.append({"code": "deadline_uses_wall_clock"})
    if '("scripts.deadline_budget_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_deadline_budget_audit"})

    return {
        "format": "x1-deadline-budget-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
