from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from scripts.download_model import (
    DEFAULT_FILE,
    DEFAULT_MODEL_NAME,
    DEFAULT_REPO,
    DEFAULT_REVISION,
    DEFAULT_SHA256,
    DEFAULT_SIZE,
    HOST_POLICY,
    MANIFEST,
    safe_context_for_ram_gib,
)

ROOT = Path(__file__).resolve().parents[1]


def test_primary_model_identity_is_single_pinned_qwen36_contract():
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    primary = manifest["primary"]
    assert manifest["format"] == "x1-llm-model-manifest-v1"
    assert primary["model_name"] == DEFAULT_MODEL_NAME == "Qwen3.6-35B-A3B-Q4_K_M"
    assert primary["repository"] == DEFAULT_REPO == "ggml-org/Qwen3.6-35B-A3B-GGUF"
    assert primary["revision"] == DEFAULT_REVISION == "3d8df365c4f151fb00629e8d9f2a2b6a4f8b441a"
    assert primary["filename"] == DEFAULT_FILE == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert primary["sha256"] == DEFAULT_SHA256 == "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7"
    assert primary["size_bytes"] == DEFAULT_SIZE == 20_419_565_568


def test_32gb_class_hosts_are_intentionally_capped_at_8k_context():
    assert safe_context_for_ram_gib(31.0) == 8192
    assert safe_context_for_ram_gib(32.0) == 8192
    assert safe_context_for_ram_gib(47.99) == 8192
    assert safe_context_for_ram_gib(48.0) == 12288
    assert safe_context_for_ram_gib(63.99) == 12288
    assert safe_context_for_ram_gib(64.0) == 16384


def test_low_ram_quant_never_changes_silently():
    policy = MANIFEST["low_ram_policy"]
    assert policy["automatic_downgrade"] is False
    assert policy["status"] == "manual_validation_required"
    assert set(policy["candidates"]) == {"Q4_K_S", "IQ4_XS"}


def test_host_policy_preserves_control_plane_memory():
    assert HOST_POLICY["minimum_detected_ram_gib"] == 31
    assert HOST_POLICY["non_llama_reserve_gib"] == 8
    assert HOST_POLICY["llama_memory_cap_gib"] == 24
    assert HOST_POLICY["llama_min_memory_gib"] == 23


def test_default_application_identity_matches_manifest():
    settings = Settings(_env_file=None)
    assert settings.llama_model_name == DEFAULT_MODEL_NAME
    assert settings.llama_model_file == DEFAULT_FILE
    assert settings.max_context_tokens == 8192
    assert settings.deep_context_tokens == 8192


def test_compose_uses_env_model_file_and_safe_32gb_defaults():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "/models/${X1_LLAMA_MODEL_FILE:-Qwen3.6-35B-A3B-Q4_K_M.gguf}" in compose
    assert "mem_limit: ${X1_LLAMA_MEMORY_LIMIT:-23g}" in compose
    assert '"${X1_DEEP_CONTEXT_TOKENS:-8192}"' in compose
    llama = compose.split("\n  llama:\n", 1)[1]
    assert "Qwen3-30B-A3B-Q4_K_M.gguf" not in llama


def test_installer_normalizes_existing_model_memory_and_context():
    installer = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    for marker in (
        "model-manifest.json",
        "safe_context_for_ram_gib",
        "X1_LLAMA_MODEL_NAME",
        "X1_LLAMA_MODEL_FILE",
        "current_memory < llama_min_gib",
        "--profile primary --verify-only",
        "--runtime --live-inference --user-journey --chaos",
    ):
        assert marker in installer
    assert "Qwen3-30B-A3B Q4_K_M production profile" not in installer


def test_downloader_verifies_exact_size_and_sha_and_supports_legacy_rollback():
    downloader = (ROOT / "scripts" / "download_model.py").read_text("utf-8")
    assert 'choices=("primary", "legacy_rollback")' in downloader
    assert "partial.stat().st_size != expected_size" in downloader
    assert "Downloaded model checksum mismatch" in downloader
    assert "os.replace(partial, target)" in downloader


def test_transactional_updater_restores_environment_and_keeps_old_model_files():
    updater = (ROOT / "scripts" / "update.sh").read_text("utf-8")
    assert 'ENV_COPY="$(mktemp -t x1-env.' in updater
    assert 'cp -p "$ENV_COPY" .env' in updater
    assert "git reset --hard \"$OLD_HEAD\"" in updater
    assert "rm -rf models" not in updater
    assert "rm -f models/" not in updater


def test_doctor_validates_model_identity_size_memory_and_context_contracts():
    doctor = (ROOT / "scripts" / "doctor.py").read_text("utf-8")
    for marker in (
        "qwen_model_identity",
        "qwen_context_budget",
        "DEFAULT_SIZE",
        "DEFAULT_SHA256",
        "LLAMA_MIN_MEMORY_GIB",
        "safe_context_for_ram_gib",
    ):
        assert marker in doctor
