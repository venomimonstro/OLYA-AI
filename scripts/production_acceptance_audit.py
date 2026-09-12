#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    source = (ROOT / "scripts/production_acceptance.py").read_text("utf-8")
    health = (ROOT / "app/api/routes/health.py").read_text("utf-8")
    provenance = (ROOT / "scripts/build_provenance.py").read_text("utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")
    roadmap = (ROOT / "docs/SPRINTS_71_86.md").read_text("utf-8")

    for token in (
        '"x1-production-acceptance-v2"',
        '"accepted_for_launch": not failed',
        "X1_PRODUCTION_ADMIN_TOKEN",
        "x1-release-candidate-v4",
        "x1-real-load-acceptance-v2",
        "x1-release-gate-v4",
        "candidate_source_provenance",
        "runtime_source_provenance",
        "public_build_provenance",
        "expected_source_fingerprint",
        "same_as_candidate",
        "git_worktree_clean",
        "working_tree_not_clean",
        "production_transport",
        "HTTPS required except loopback acceptance",
        "billing_runtime_config",
        "payment_ingest_secret",
        "checkout_url(s,'production-acceptance-probe')",
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

    for token in ('@router.get("/version")', "BUILD_PROVENANCE.json", "source_fingerprint"):
        if token not in health:
            errors.append({"code": "runtime_provenance_endpoint_missing", "token": token})
    for token in ("x1-build-provenance-v1", "build-inputs/Dockerfile", "build-inputs/docker-compose.yml"):
        if token not in provenance:
            errors.append({"code": "build_provenance_contract_missing", "token": token})
    if "python -m scripts.build_provenance" not in dockerfile or "/app/BUILD_PROVENANCE.json" not in dockerfile:
        errors.append({"code": "docker_provenance_embedding_missing"})

    if "shell=True" in source:
        errors.append({"code": "production_acceptance_shell_execution_forbidden"})
    if "admin-token" in source and "--admin-token-env" not in source:
        errors.append({"code": "admin_token_must_come_from_environment"})
    if '("scripts.production_acceptance_audit",[])' not in regression and '("scripts.production_acceptance_audit", [])' not in regression:
        errors.append({"code": "regression_missing_production_acceptance_audit"})
    if "PRODUCTION RUN REQUIRED" not in roadmap:
        errors.append({"code": "roadmap_must_not_claim_unrun_production_acceptance"})

    return {
        "format": "x1-production-acceptance-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "requires_external_production_run": True,
        "requires_clean_worktree": True,
        "requires_runtime_build_provenance": True,
        "requires_external_https_or_loopback": True,
        "requires_live_billing_configuration": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
