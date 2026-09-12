#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    beta = (ROOT / "app/services/image_beta.py").read_text("utf-8")
    studio = (ROOT / "app/image_studio_ui.py").read_text("utf-8")
    images = (ROOT / "app/api/routes/images.py").read_text("utf-8")
    editing = (ROOT / "app/api/routes/image_editing.py").read_text("utf-8")
    capabilities = (ROOT / "app/services/capabilities.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    required = {
        "canonical_generation": (beta, 'capability_decision(app, db, user, "images.generate"'),
        "canonical_editing": (beta, 'capability_decision(app, db, user, "images.edit"'),
        "privacy_no_store": (beta, '"reference_delivery": "private_no_store"'),
        "training_opt_in": (beta, '"training_requires_explicit_feedback_consent": True'),
        "qa_delivery_gate": (beta, '"delivery_requires_qa_passed": True'),
        "beta_endpoint": (studio, '@router.get("/v1/images/beta-contract")'),
        "studio_uses_beta": (studio, "'/v1/images/beta-contract'"),
        "studio_fail_closed": (studio, "Capability contract недоступен — запуск заблокирован."),
        "studio_rechecks_before_run": (studio, "await readiness();if(!ready)"),
        "studio_bounded_poll": (studio, "for(let i=0;i<240;i++)"),
        "strict_qa_request": (studio, "strict_quality:true"),
        "generation_worker": (capabilities, '"image_worker"'),
        "storage_quota": (capabilities, '"image_storage_quota"'),
        "active_slot": (capabilities, '"image_active_slot"'),
        "qa_content_gate": (images, 'row.status != "ready" or row.qa_status != "passed"'),
        "reference_private": (editing, '"Cache-Control": "private, no-store"'),
        "feedback_consent": (images, "allow_training"),
    }
    for code, (source, token) in required.items():
        if token not in source:
            errors.append({"code": code, "token": token})

    for forbidden in ("localStorage", "'unsafe-inline'", " onclick=", " style="):
        if forbidden in studio:
            errors.append({"code": "unsafe_image_studio_pattern", "token": forbidden})
    if "script-src 'nonce-" not in studio or "style-src 'nonce-" not in studio:
        errors.append({"code": "image_studio_nonce_csp_missing"})
    if '("scripts.image_studio_beta_audit", [])' not in regression:
        errors.append({"code": "full_regression_missing_image_studio_beta_audit"})

    return {"format": "x1-image-studio-beta-audit-v1", "status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
