from __future__ import annotations

import json
import tarfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.main import app
from app.services import system_observability as obs
from scripts.long_context_probe import offline_probe
from scripts.run_full_regression import EXPECTED


def _settings(tmp_path: Path, *, env: str = "production") -> SimpleNamespace:
    return SimpleNamespace(
        env=env,
        release_gate_report_path=str(tmp_path / "release-gate-latest.json"),
        restore_drill_report_path=str(tmp_path / "restore-drill-latest.json"),
        release_gate_max_age_hours=24.0,
        restore_drill_max_age_hours=168.0,
    )


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), "utf-8")


def test_release_gate_requires_runtime_live_inference_in_production(tmp_path: Path):
    settings = _settings(tmp_path)
    path = Path(settings.release_gate_report_path)
    _write(
        path,
        {
            "status": "passed",
            "version": "0.35.0",
            "mode": "static",
            "live_inference_requested": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    result = obs._release_gate_check(settings, app_version="0.35.0")
    assert result["status"] == obs.DEGRADED
    assert "runtime_gate_not_run" in result["details"]["reasons"]
    assert "live_inference_not_run" in result["details"]["reasons"]

    payload = json.loads(path.read_text("utf-8"))
    payload["mode"] = "runtime"
    payload["live_inference_requested"] = True
    _write(path, payload)
    assert obs._release_gate_check(settings, app_version="0.35.0")["status"] == obs.STABLE


def test_release_gate_rejects_stale_or_wrong_version_report(tmp_path: Path):
    settings = _settings(tmp_path)
    _write(
        Path(settings.release_gate_report_path),
        {
            "status": "passed",
            "version": "0.34.0",
            "mode": "runtime",
            "live_inference_requested": True,
            "finished_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        },
    )
    result = obs._release_gate_check(settings, app_version="0.35.0")
    assert result["status"] == obs.DEGRADED
    assert "version_mismatch" in result["details"]["reasons"]
    assert "stale" in result["details"]["reasons"]


def test_restore_drill_report_has_freshness_gate(tmp_path: Path):
    settings = _settings(tmp_path)
    path = Path(settings.restore_drill_report_path)
    _write(
        path,
        {
            "status": "passed",
            "backup": "/backups/example",
            "alembic_version": "head",
            "restored_table_count": 42,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    result = obs._restore_drill_check(settings)
    assert result["status"] == obs.STABLE
    assert result["details"]["restored_table_count"] == 42


def test_long_context_offline_release_probe_preserves_both_ends():
    result = offline_probe(16384, 2200)
    assert result["status"] == "passed"
    assert result["kept_head_marker"] is True
    assert result["kept_tail_marker"] is True
    assert result["compiled_chars"] <= result["prompt_budget_chars"]


def test_release_gate_container_isolated_and_image_worker_has_extras():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text("utf-8")
    dockerfile = (root / "Dockerfile").read_text("utf-8")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))

    gate_section = compose.split("\n  gate:\n", 1)[1].split("\n  image-worker:\n", 1)[0]
    assert 'X1_EXTRAS: "dev"' in gate_section
    assert "x1_data:/app/data" not in gate_section
    image_section = compose.split("\n  image-worker:\n", 1)[1].split("\n  llama:\n", 1)[0]
    assert 'X1_EXTRAS: "image"' in image_section
    assert "image" in pyproject["project"]["optional-dependencies"]
    assert "ARG X1_EXTRAS" in dockerfile
    assert "USER x1" in dockerfile


def test_restore_drill_is_non_destructive_and_checks_archive_safety():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "restore_drill.sh").read_text("utf-8")
    assert "x1_restore_drill_" in script
    assert "dropdb -U x1 --if-exists" in script
    assert "archive.extractall(destination, filter=\"data\")" in script
    assert "member.issym()" in script
    assert 'DB_NAME="x1_restore_drill_' in script


def test_historical_regression_bundle_has_all_45_modules():
    root = Path(__file__).resolve().parents[1]
    archive_path = root / "tests" / "legacy_sprint0_26.tar.gz"
    assert archive_path.is_file() and archive_path.stat().st_size > 10_000
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {
            Path(member.name).name
            for member in archive.getmembers()
            if member.isfile() and Path(member.name).name.startswith("test_")
        }
    assert len(EXPECTED) == 45
    assert names == EXPECTED


def test_release_gate_runs_full_regression_and_final_readiness():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "release_gate.py").read_text("utf-8")
    assert '"pytest_full"' in script
    assert "scripts.run_full_regression" in script
    assert '"restore_drill"' in script
    assert '"long_context_live"' in script
    assert "final_ready_probe" in script
    assert '"containerized_gate"' in script
    assert '"historical_regression_modules": 45' in script


def test_application_and_package_versions_match():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    assert data["project"]["version"] == app.version == "0.35.0"


def test_admin_release_readiness_route_is_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v1/admin/reliability/release-readiness" in paths
