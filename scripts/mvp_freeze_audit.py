#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    freeze_path = ROOT / "docs" / "MVP_FREEZE.json"
    rc = (ROOT / "scripts" / "rc_release_candidate.py").read_text("utf-8")
    load = (ROOT / "scripts" / "load_acceptance.py").read_text("utf-8")
    regression = (ROOT / "scripts" / "run_full_regression.py").read_text("utf-8")

    if not freeze_path.is_file():
        errors.append({"code": "freeze_manifest_missing"})
        freeze = {}
    else:
        try:
            freeze = json.loads(freeze_path.read_text("utf-8"))
        except Exception as exc:
            freeze = {}
            errors.append({"code": "freeze_manifest_invalid", "error": type(exc).__name__})

    if freeze.get("format") != "x1-mvp-freeze-v1":
        errors.append({"code": "freeze_format_invalid"})
    if freeze.get("freeze_after_sprint") != 84 or freeze.get("feature_state") != "frozen":
        errors.append({"code": "freeze_boundary_invalid"})
    required_rc = set(freeze.get("release_candidate_requires") or [])
    for item in (
        "full_regression",
        "security_audit",
        "business_logic_contract",
        "current_head_release_gate_evidence",
        "current_head_target_load_evidence",
        "model_regression_evidence",
        "backup_restore_drill",
        "runtime_chaos",
        "capacity_calibration",
    ):
        if item not in required_rc:
            errors.append({"code": "freeze_rc_requirement_missing", "requirement": item})

    for token in (
        "x1-release-candidate-v3",
        "business_logic_contract_audit",
        "target_load_evidence",
        "x1-real-load-acceptance-v1",
        "verify_load_head()",
        "verify_release_head()",
        "restore_drill_evidence",
        "runtime_chaos_evidence",
        '"feature_freeze_after_sprint": 84',
    ):
        if token not in rc:
            errors.append({"code": "rc_gate_token_missing", "token": token})

    for token in (
        "backups/load-acceptance-latest.json",
        '"git_head": current_git_head()',
        "write_report(path, result)",
    ):
        if token not in load:
            errors.append({"code": "load_evidence_token_missing", "token": token})

    if '("scripts.mvp_freeze_audit",[])' not in regression and '("scripts.mvp_freeze_audit", [])' not in regression:
        errors.append({"code": "regression_missing_mvp_freeze_audit"})

    return {
        "format": "x1-mvp-freeze-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "freeze_after_sprint": freeze.get("freeze_after_sprint"),
        "rc_requires_external_target_evidence": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
