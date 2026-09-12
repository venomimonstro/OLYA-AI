#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    service = (ROOT / "app/services/business_contract.py").read_text("utf-8")
    reliability = (ROOT / "app/api/routes/reliability.py").read_text("utf-8")
    ownership = (ROOT / "app/domain_ownership.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required_service_tokens = (
        'CONTRACT_VERSION = "x1-business-contract-v1"',
        "runtime_plan_catalog",
        "control_is_active",
        "completion_blockers",
        "agent_completion_blockers",
        '"quota_plan_mismatch"',
        '"api_key_org_access_revoked"',
        '"completed_task_evidence_invalid"',
        '"agent_plan_completion_unproven"',
    )
    for token in required_service_tokens:
        if token not in service:
            errors.append({"code": "business_contract_token_missing", "token": token})

    for token in (
        '@router.get("/business-contract")',
        "evaluate_business_contract",
        '"business.logic_contract"',
        '"business_contract": business',
    ):
        if token not in reliability:
            errors.append({"code": "release_integration_missing", "token": token})

    if '"business_logic_contract": "app.services.business_contract"' not in ownership:
        errors.append({"code": "business_contract_owner_missing"})
    if '("scripts.business_logic_contract_audit",[])' not in regression and '("scripts.business_logic_contract_audit", [])' not in regression:
        errors.append({"code": "regression_missing_business_contract"})

    try:
        module = importlib.import_module("app.services.business_contract")
        if module.CONTRACT_VERSION != "x1-business-contract-v1":
            errors.append({"code": "business_contract_version_invalid"})
    except Exception as exc:
        errors.append({"code": "business_contract_import_failed", "error": type(exc).__name__})

    return {
        "format": "x1-business-logic-contract-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
