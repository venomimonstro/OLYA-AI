from __future__ import annotations

from pathlib import Path

from scripts.canonical_source_audit import audit_all

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text("utf-8")


def test_image_edits_share_image_worker_budget_and_dead_worker_admission():
    source = read("app/services/auth.py")
    assert 'path == "/v1/images/generations"' in source
    assert 'path == "/v1/images/edits"' in source
    assert source.count('channel = "image_worker"') >= 2
    assert "image_worker_snapshot" in source
    assert "image_worker_unavailable" in source
    assert 'headers={"Retry-After": "5"}' in source


def test_all_image_creation_surfaces_share_bounded_overload_lane():
    source = read("app/main.py")
    assert '"/v1/images/generations"' in source
    assert '"/v1/images/edits"' in source
    assert '"/v1/images/references"' in source
    assert 'return "images"' in source


def test_maintenance_recovers_cross_service_interrupted_states_before_retention():
    source = read("app/services/maintenance.py")
    for marker in (
        "_recover_interrupted_image_jobs",
        "_recover_stale_file_processing",
        "_recover_stale_document_qa",
        'ImageGeneration.status.in_(["queued", "generating"])',
        'ProjectFile.status == "processing"',
        'DocumentRevision.qa_status == "running"',
        'gate="qa_recovered"',
        'revision.qa_status = "pending"',
    ):
        assert marker in source
    assert source.index('counts["recovered_image_jobs"]') < source.index('counts["failed_jobs"]')


def test_terminal_image_job_failure_cannot_leave_edit_polling_forever():
    source = read("app/services/maintenance.py")
    assert 'BackgroundJob.status == "failed"' in source
    assert 'generation.status = "failed"' in source
    assert 'generation.qa_status = "failed"' in source
    assert 'edit.status = "failed"' in source
    assert '"recovered_after_worker_failure": True' in source
    assert 'ImageGeneration.job_id.is_(None)' in source


def test_canonical_compressed_sources_are_actually_decoded_and_compiled():
    result = audit_all()
    assert result["status"] == "passed", result
    assert not result["errors"]
    checked = {item["path"] for item in result["checks"]}
    assert checked == {
        "app/_models_impl.py.gz",
        "app/services/engineering_execution.py",
        "app/services/image_runtime.py",
    }
    for item in result["checks"]:
        assert item["bytes"] > 0
        assert len(item["sha256"]) == 64
        assert item["top_level_nodes"] > 0


def test_full_regression_runs_canonical_integrity_before_pytest():
    source = read("scripts/run_full_regression.py")
    assert '"scripts.canonical_source_audit"' in source
    assert source.index('"scripts.canonical_source_audit"') < source.index('"scripts.model_regression_lab"')
    assert source.index('"scripts.canonical_source_audit"') < source.index('"pytest"')


def test_project_file_and_document_recovery_remain_retryable_not_silently_successful():
    source = read("app/services/maintenance.py")
    assert 'row.status = "error"' in source
    assert 'row.is_current = False' in source
    assert 'artifact.status = "draft"' in source
    assert 'status="failed"' in source
    assert "returned to pending for a safe retry" in source
