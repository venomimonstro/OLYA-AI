#!/usr/bin/env python3
from __future__ import annotations

import json

# Importing the route package installs runtime policy patches before route
# modules bind quota/budget functions.
import app.api.routes  # noqa: F401
from app.services import budget_transparency, quota
from app.testing_unlimited_usage_patch import testing_unlimited_usage_enabled


def audit() -> dict:
    errors: list[str] = []
    enabled = testing_unlimited_usage_enabled()
    quota_bypass = bool(getattr(quota.ensure_compute_available, "_olya_testing_unlimited_usage", False))
    units_bypass = bool(getattr(quota.request_unit_usage, "_olya_testing_unlimited_usage", False))
    budget_bypass = bool(getattr(budget_transparency.budget_snapshot, "_olya_testing_unlimited_usage", False))

    if not enabled:
        errors.append("testing_unlimited_usage_disabled")
    if not quota_bypass:
        errors.append("compute_quota_bypass_not_installed")
    if not units_bypass:
        errors.append("request_limit_bypass_not_installed")
    if not budget_bypass:
        errors.append("budget_preview_bypass_not_installed")

    return {
        "format": "olya-testing-unlimited-usage-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "testing_unlimited_usage": enabled,
        "request_count_limits": "disabled",
        "monthly_compute_limit": "disabled",
        "measured_resource_limit": "disabled",
        "budget_preview_block": "disabled",
        "physical_server_guards": "unchanged",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
