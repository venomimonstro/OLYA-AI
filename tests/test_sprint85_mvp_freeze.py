import json
from pathlib import Path

from scripts.mvp_freeze_audit import audit

ROOT = Path(__file__).resolve().parents[1]


def test_mvp_freeze_manifest_is_frozen_after_sprint84():
    payload = json.loads((ROOT / "docs/MVP_FREEZE.json").read_text("utf-8"))
    assert payload["format"] == "x1-mvp-freeze-v1"
    assert payload["freeze_after_sprint"] == 84
    assert payload["feature_state"] == "frozen"
    assert "current_head_target_load_evidence" in payload["release_candidate_requires"]


def test_mvp_freeze_audit_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]
