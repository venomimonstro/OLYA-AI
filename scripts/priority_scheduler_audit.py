#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    scheduler = (ROOT / "app/services/resource_governor.py").read_text("utf-8")
    quota = (ROOT / "app/services/quota.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required_scheduler = [
        '"fast": 0.0',
        '"work": 10.0',
        '"api": 12.0',
        '"deep": 20.0',
        '"background": 30.0',
        '"business": -4.0',
        'age_points = max(0.0, now - waiter.enqueued_at) / 2.0',
        'if self._active < self.max_concurrent',
        'if len(self._waiters) >= self.max_queue',
        'deadline = loop.time() + self.wait_timeout_seconds',
        'set_inference_scheduler_context',
        'inference_scheduler_context',
        '"waiting_by_priority"',
        '"oldest_wait_seconds"',
    ]
    for token in required_scheduler:
        if token not in scheduler:
            errors.append({"code": "priority_scheduler_contract_missing", "token": token})

    required_quota = [
        "set_inference_scheduler_context",
        "priority_class=priority",
        "plan=quota.plan",
        "principal=user.id",
        "channel=scheduler_channel",
        "current_channel_override()",
    ]
    for token in required_quota:
        if token not in quota:
            errors.append({"code": "scheduler_context_not_propagated", "token": token})

    # Priority must never replace the existing safety/resource admission chain.
    if quota.find("Monthly local compute budget exhausted") > quota.find("set_inference_scheduler_context"):
        errors.append({"code": "scheduler_context_precedes_compute_quota"})
    if quota.find("ensure_channel_budget") > quota.find("set_inference_scheduler_context"):
        errors.append({"code": "scheduler_context_precedes_channel_budget"})

    if '("scripts.priority_scheduler_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_priority_scheduler_audit"})

    return {
        "format": "x1-priority-scheduler-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
