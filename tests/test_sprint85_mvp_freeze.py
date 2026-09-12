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
    assert "current_source_runtime_provenance" in payload["release_candidate_requires"]


def test_release_candidate_uses_manifest_ram_policy_and_clean_source():
    source = (ROOT / "scripts/rc_release_candidate.py").read_text("utf-8")
    assert "minimum_supported_ram_gib()" in source
    assert '"supported_host_ram"' in source
    assert "verify_clean_worktree()" in source
    assert "verify_runtime_source()" in source
    assert "verify_load_identity()" in source
    assert "31.0 <= ram < 40.0" not in source


def test_mvp_freeze_audit_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]
    assert result["runtime_build_provenance_required"] is True
