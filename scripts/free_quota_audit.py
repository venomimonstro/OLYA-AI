#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    quota = (ROOT / "app" / "services" / "quota.py").read_text("utf-8")
    errors: list[dict] = []
    required = {
        "daily_limit": "_TEMP_FREE_DAILY_REQUEST_LIMIT = 30",
        "monthly_limit": "_TEMP_FREE_MONTHLY_REQUEST_LIMIT = 930",
        "free_early_return": 'if quota.plan == "free":',
        "request_gate": "_ensure_request_units_available(db, user, settings, quota.plan, request_mode)",
        "free_returns_before_paid_budgets": "return quota\n\n    used = compute_seconds_used",
    }
    for code, token in required.items():
        if token not in quota:
            errors.append({"code": code, "missing": token})

    free_block_start = quota.find('if quota.plan == "free":')
    paid_compute_start = quota.find("used = compute_seconds_used", free_block_start)
    free_block = quota[free_block_start:paid_compute_start] if free_block_start >= 0 and paid_compute_start > free_block_start else ""
    for forbidden in (
        "ensure_channel_budget(",
        "Monthly measured resource budget exhausted",
        "Monthly local compute budget exhausted",
    ):
        if forbidden in free_block:
            errors.append({"code": "hidden_free_resource_cap", "token": forbidden})

    return {
        "format": "olya-free-quota-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "daily_successful_requests": 30,
        "monthly_successful_requests": 930,
        "hidden_channel_share_caps": False,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
