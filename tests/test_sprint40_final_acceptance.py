from __future__ import annotations

import tomllib
from pathlib import Path

from app.services.source_context import DEFAULT_FRESHNESS_MAX_AGE_SECONDS
from scripts.download_model import DEFAULT_FILE, DEFAULT_REPO, DEFAULT_REVISION, DEFAULT_SHA256, DEFAULT_SIZE, DEFAULT_URL

ROOT = Path(__file__).resolve().parents[1]


def test_qwen_artifact_is_immutable_and_checksum_pinned():
    assert DEFAULT_REPO == "ggml-org/Qwen3.6-35B-A3B-GGUF"
    assert DEFAULT_REVISION == "3d8df365c4f151fb00629e8d9f2a2b6a4f8b441a"
    assert DEFAULT_FILE == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert DEFAULT_SHA256 == "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
    assert DEFAULT_SIZE == 20_419_565_568
    assert f"/resolve/{DEFAULT_REVISION}/{DEFAULT_FILE}" in DEFAULT_URL
    assert "/resolve/main/" not in DEFAULT_URL


def test_current_fact_evidence_expires_quickly_by_default():
    assert DEFAULT_FRESHNESS_MAX_AGE_SECONDS == 15 * 60


def test_release_gate_requires_separate_component_acceptance():
    gate = (ROOT / "scripts" / "release_gate.py").read_text("utf-8")
    assert 'run("component_acceptance"' in gate
    assert "scripts.component_acceptance" in gate
    assert '"component_acceptance_requested": bool(args.runtime)' in gate
    assert gate.index('run("component_acceptance"') < gate.index('run("e2e_user_journey"')


def test_final_doctor_does_not_depend_on_wget_and_compares_migration_heads():
    doctor = (ROOT / "scripts" / "doctor.py").read_text("utf-8")
    assert "searxng\", \"python\", \"-c\"" in doctor
    assert "searxng\", \"sh\", \"-c\", \"wget" not in doctor
    assert "alembic\", \"current\"" in doctor
    assert "alembic\", \"heads\"" in doctor
    assert "current_tokens == head_tokens" in doctor
    assert "_service_section(rendered, \"app\")" in doctor


def test_transactional_updater_uses_one_target_version_recovery_toolset():
    update = (ROOT / "scripts" / "update.sh").read_text("utf-8")
    for path in (
        "origin/main:scripts/backup.sh",
        "origin/main:scripts/restore_drill.sh",
        "origin/main:scripts/restore.sh",
        "origin/main:scripts/migrate_legacy_data.sh",
    ):
        assert path in update
    assert 'X1_INSTALL_DIR="$ROOT" bash "$MIGRATE_COPY"' in update
    assert 'bash "$BACKUP_COPY"' in update
    assert 'bash "$DRILL_COPY" "$BACKUP_PATH"' in update
    assert 'bash "$RESTORE_COPY" "$BACKUP_PATH"' in update
    assert 'cp -p "$ENV_COPY" .env' in update
    assert update.index("Checking for legacy named-volume user data") < update.index("Creating consistent pre-update backup") < update.index("Fast-forwarding code")


def test_production_core_dependencies_are_exactly_pinned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    direct = list(project["project"]["dependencies"])
    assert direct
    assert all("==" in item for item in direct)
