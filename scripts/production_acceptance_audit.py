#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    source = (ROOT / "scripts/production_acceptance.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")
    roadmap = (ROOT / "docs/SPRINTS_71_86.md").read_text("utf-8")

    for token in (
        '"x1-production-acceptance-v1"',
        '"accepted_for_launch": not failed',
        "X1_PRODUCTION_ADMIN_TOKEN",
        "x1-release-candidate-v3",
        "x1-real-load-acceptance-v1",
        "x1-release-gate-v4",
        "postgresql_ready",
        "qwen_llama_health",
        "searxng_health",
        "sandbox_worker_health",
        "document_worker_health",
        "/v1/admin/reliability/release-readiness?refresh=true",
        "/v1/admin/reliability/business-contract",
        "/v1/admin/capabilities?live=true",
        '"chat", "files", "documents", "research.search", "sandbox.execute", "development", "api", "billing"',
        "return 0 if payload[\"accepted_for_launch\"] else 2",
    ):
        if token not in source:
            errors.append({"code": "production_acceptance_token_missing", "token": token})

    if "shell=True" in source:
        errors.append({"code": "production_acceptance_shell_execution_forbidden"})
    if "admin-token" in source and "--admin-token-env" not in source:
        errors.append({"code": "admin_token_must_come_from_environment"})
    if '("scripts.production_acceptance_audit",[])' not in regression and '("scripts.production_acceptance_audit", [])' not in regression:
        errors.append({"code": "regression_missing_production_acceptance_audit"})
    if "PRODUCTION RUN REQUIRED" not in roadmap:
        errors.append({"code": "roadmap_must_not_claim_unrun_production_acceptance"})

    return {
        "format": "x1-production-acceptance-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "requires_external_production_run": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
