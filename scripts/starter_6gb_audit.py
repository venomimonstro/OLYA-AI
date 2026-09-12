#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    config = (ROOT / "app/core/config.py").read_text("utf-8")
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    quota = (ROOT / "app/services/quota.py").read_text("utf-8")
    plans = (ROOT / "app/services/measured_plans.py").read_text("utf-8")
    governor = (ROOT / "app/services/resource_governor.py").read_text("utf-8")
    router = (ROOT / "app/inference/router.py").read_text("utf-8")
    verification = (ROOT / "app/services/conditional_verification.py").read_text("utf-8")
    installer = (ROOT / "scripts/install_starter_6gb.sh").read_text("utf-8")
    runtime_audit = (ROOT / "scripts/runtime_config_audit.py").read_text("utf-8")

    primary = manifest.get("primary") or {}
    host = manifest.get("host_policy") or {}
    low = manifest.get("low_ram_policy") or {}
    if primary.get("model_name") != "Qwen3-4B-Q4_K_M" or primary.get("quantization") != "Q4_K_M":
        errors.append({"code": "starter_primary_model_mismatch"})
    if int(primary.get("size_bytes") or 0) <= 0 or int(primary.get("size_bytes") or 0) > 3_000_000_000:
        errors.append({"code": "starter_model_size_out_of_envelope", "size_bytes": primary.get("size_bytes")})
    if int(host.get("minimum_detected_ram_gib") or 0) < 8:
        errors.append({"code": "generic_full_install_must_not_target_6gb"})
    if low.get("starter_profile") != "starter_6gb" or int(low.get("primary_target_ram_gib") or 0) != 6:
        errors.append({"code": "starter_low_ram_policy_missing"})
    if int(low.get("minimum_detected_ram_gib") or 0) > 5 or int(low.get("context_tokens") or 0) != 4096:
        errors.append({"code": "starter_low_ram_envelope_invalid"})
    if str(low.get("llama_memory_limit") or "") != "3200m":
        errors.append({"code": "starter_llama_memory_policy_invalid"})

    for token in (
        'llama_model_name: str = "Qwen3-4B-Q4_K_M"',
        "max_context_tokens: int = 4096",
        "deep_context_tokens: int = 4096",
        "max_concurrent_generations: int = 1",
        "max_queue_size: int = 16",
        "inference_max_queued_per_principal: int = 1",
        "plan_monthly_request_units_free: int = 30",
        "plan_monthly_request_units_x1: int = 240",
        "plan_daily_request_units_free: int = 6",
        "request_unit_weight_deep: int = 4",
    ):
        if token not in config:
            errors.append({"code": "starter_config_contract_missing", "token": token})

    for token in (
        "${X1_DB_MEMORY_LIMIT_MB:-384}",
        "${X1_SEARX_MEMORY_LIMIT_MB:-256}",
        "${X1_APP_MEMORY_LIMIT_MB:-768}",
        "${X1_LLAMA_MEMORY_LIMIT:-3200m}",
        "/models/${X1_LLAMA_MODEL_FILE:-Qwen3-4B-Q4_K_M.gguf}",
        "${X1_DEEP_CONTEXT_TOKENS:-4096}",
        "${X1_MAX_CONCURRENT_GENERATIONS:-1}",
        "q4_0",
    ):
        if token not in compose:
            errors.append({"code": "starter_compose_contract_missing", "token": token})
    if "--mmproj" in compose:
        errors.append({"code": "starter_text_model_must_not_require_mmproj"})

    for token in (
        "max_queued_per_principal",
        "principal_rejections",
        "this account already has a request waiting for local inference",
    ):
        if token not in governor:
            errors.append({"code": "starter_queue_fairness_missing", "token": token})
    for token in ("monthly_request_units", "daily_request_units", "request_unit_weights"):
        if token not in plans or token not in quota:
            errors.append({"code": "starter_request_quota_missing", "token": token})
    for token in ("UsageEvent.success.is_(True)", "request_mode = _inferred_channel(reserve_seconds)"):
        if token not in quota:
            errors.append({"code": "starter_request_unit_fairness_missing", "token": token})
    if "starter_4k = deep_limit <= 4096" not in router or "max_output_tokens=1024 if starter_4k" not in router:
        errors.append({"code": "starter_output_budget_missing"})
    if "return 1" not in verification or "repair_critic=False" not in verification:
        errors.append({"code": "starter_verification_must_be_single_extra_pass"})

    for token in (
        "X1_SERVER_OPTIMIZATION_PROFILE','starter_6gb'",
        "X1_PROJECT_SANDBOX_BACKEND','disabled'",
        "X1_DOCUMENT_RENDER_BACKEND','disabled'",
        "X1_LLAMA_MEMORY_LIMIT','3200m'",
        "X1_DATABASE_POOL_SIZE','3'",
        "scripts.starter_6gb_audit",
        "--build-arg X1_RUNTIME_PROFILE=starter_6gb",
    ):
        if token not in installer:
            errors.append({"code": "starter_installer_contract_missing", "token": token})
    for token in (
        'starter = str(settings.server_optimization_profile).lower() == "starter_6gb"',
        'document_backend in {"disabled", "remote"}',
        'sandbox_backend in {"disabled", "remote"}',
        'starter_inference_concurrency_must_be_one',
    ):
        if token not in runtime_audit:
            errors.append({"code": "starter_runtime_audit_contract_missing", "token": token})

    return {
        "format": "x1-starter-6gb-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "target": {"cpu_cores": 4, "ram_gib": 6, "disk_gib": 80},
        "model": primary.get("model_name"),
        "one_active_inference": True,
        "bounded_queue": True,
        "request_unit_billing": True,
        "heavy_workers_disabled_by_starter_installer": True,
        "generic_full_install_min_ram_gib": int(host.get("minimum_detected_ram_gib") or 0),
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
