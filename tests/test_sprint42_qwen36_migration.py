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


def test_primary_model_identity_is_pinned_official_qwen4b_contract():
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    primary = manifest["primary"]
    assert manifest["format"] == "x1-llm-model-manifest-v1"
    assert primary["model_name"] == DEFAULT_MODEL_NAME == "Qwen3-4B-Q4_K_M"
    assert primary["family"] == "Qwen3-4B"
    assert primary["quantization"] == "Q4_K_M"
    assert primary["repository"] == DEFAULT_REPO == "Qwen/Qwen3-4B-GGUF"
    assert primary["revision"] == DEFAULT_REVISION == "a9a60d009fa7ff9606305047c2bf77ac25dbec49"
    assert primary["filename"] == DEFAULT_FILE == "Qwen3-4B-Q4_K_M.gguf"
    assert primary["sha256"] == DEFAULT_SHA256 == "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
    assert primary["size_bytes"] == DEFAULT_SIZE == 2_497_280_256


def test_host_context_policy_matches_small_cpu_ram_deployment():
    assert safe_context_for_ram_gib(8.0) == 8192
    assert safe_context_for_ram_gib(15.99) == 8192
    assert safe_context_for_ram_gib(16.0) == 12288
    assert safe_context_for_ram_gib(64.0) == 12288


def test_low_ram_profile_is_explicit_qwen4b_not_silent_downgrade():
    policy = MANIFEST["low_ram_policy"]
    assert policy["automatic_downgrade"] is False
    assert policy["starter_profile"] == "starter_6gb"
    assert policy["minimum_detected_ram_gib"] == 5
    assert policy["primary_target_ram_gib"] == 6
    assert policy["context_tokens"] == 4096
    assert policy["llama_memory_limit"] == "3200m"


def test_host_policy_preserves_control_plane_memory():
    assert HOST_POLICY["minimum_detected_ram_gib"] == 8
    assert HOST_POLICY["non_llama_reserve_gib"] == 2
    assert HOST_POLICY["llama_memory_cap_gib"] == 6
    assert HOST_POLICY["llama_min_memory_gib"] == 3


def test_default_application_identity_matches_qwen4b_starter():
    settings = Settings(_env_file=None)
    assert settings.llama_model_name == DEFAULT_MODEL_NAME
    assert settings.max_context_tokens == 4096
    assert settings.deep_context_tokens == 4096
    assert settings.max_concurrent_generations == 1


def test_compose_uses_qwen4b_and_low_ram_safe_defaults():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "/models/${X1_LLAMA_MODEL_FILE:-Qwen3-4B-Q4_K_M.gguf}" in compose
    assert "mem_limit: ${X1_LLAMA_MEMORY_LIMIT:-3200m}" in compose
    assert '"${X1_DEEP_CONTEXT_TOKENS:-4096}"' in compose
    assert '"${X1_MAX_CONCURRENT_GENERATIONS:-1}"' in compose
    llama = compose.split("\n  llama:\n", 1)[1]
    assert "Qwen3-2B" not in llama


def test_starter_installer_normalizes_existing_2b_environment_to_4b():
    installer = (ROOT / "scripts" / "install_starter_6gb.sh").read_text("utf-8")
    for marker in (
        "Qwen3-4B-Q4_K_M",
        "X1_LLAMA_MODEL_NAME",
        "X1_LLAMA_MODEL_FILE",
        "X1_LLAMA_MEMORY_LIMIT",
        "X1_MAX_CONTEXT_TOKENS",
        "--profile primary --verify-only",
    ):
        assert marker in installer
    assert "Qwen3-2B" not in installer


def test_dedicated_qwen4b_upgrade_is_safe_and_verified():
    upgrade = (ROOT / "scripts" / "upgrade_qwen4b.sh").read_text("utf-8")
    for marker in (
        "env-before-qwen4b",
        "Qwen3-4B-Q4_K_M",
        "scripts/download_model.py --profile primary",
        "--verify-only",
        "--force-recreate llama",
        "X1_REQUEST_TIMEOUT_SECONDS', '300'",
        "X1_MAX_CONCURRENT_GENERATIONS', '1'",
        "smoke test",
    ):
        assert marker in upgrade


def test_downloader_verifies_exact_size_and_sha_and_keeps_rollback_profile():
    downloader = (ROOT / "scripts" / "download_model.py").read_text("utf-8")
    assert '"legacy_rollback"' in downloader
    assert "partial.stat().st_size != expected_size" in downloader
    assert "Downloaded {name} checksum mismatch" in downloader
    assert "os.replace(partial, target)" in downloader


def test_transactional_updater_restores_environment_and_keeps_old_model_files():
    updater = (ROOT / "scripts" / "update.sh").read_text("utf-8")
    assert 'ENV_COPY="$(mktemp -t x1-env.' in updater
    assert 'cp -p "$ENV_COPY" .env' in updater
    assert "git reset --hard \"$OLD_HEAD\"" in updater
    assert "rm -rf models" not in updater
    assert "rm -f models/" not in updater
