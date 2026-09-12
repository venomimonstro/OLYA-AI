from pathlib import Path

from scripts.production_acceptance_audit import audit

ROOT = Path(__file__).resolve().parents[1]


def test_production_acceptance_harness_exists():
    source = (ROOT / "scripts/production_acceptance.py").read_text("utf-8")
    assert "x1-production-acceptance-v2" in source
    assert "accepted_for_launch" in source
    assert "X1_PRODUCTION_ADMIN_TOKEN" in source
    assert "runtime_source_provenance" in source
    assert "public_build_provenance" in source
    assert "git_worktree_clean" in source
    assert "billing_runtime_config" in source


def test_production_acceptance_audit_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]
    assert result["requires_external_production_run"] is True
    assert result["requires_runtime_build_provenance"] is True
