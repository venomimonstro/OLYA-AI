#!/usr/bin/env python3
from __future__ import annotations

import json

from app.core.config import get_settings
from app.services.image_capabilities import SUPPORTED_EDIT_BACKENDS
from app.services.image_vision_endpoint import (
    VisionEndpointError,
    is_self_hosted_vision_origin,
    validate_vision_endpoint,
)


def validate(settings) -> list[str]:
    errors: list[str] = []

    def require(condition: bool, code: str) -> None:
        if not condition:
            errors.append(code)

    require(int(settings.max_context_tokens) >= 1024, "max_context_too_small")
    require(int(settings.deep_context_tokens) >= int(settings.max_context_tokens), "deep_context_below_normal_context")
    require(int(settings.default_max_output_tokens) > 0, "default_output_tokens_not_positive")
    require(int(settings.default_max_output_tokens) + 512 < int(settings.deep_context_tokens), "output_budget_leaves_no_prompt_headroom")
    require(int(settings.max_concurrent_generations) >= 1, "inference_concurrency_not_positive")
    require(int(settings.max_queue_size) >= 1, "inference_queue_not_positive")

    require(int(settings.file_chunk_chars) >= 256, "file_chunk_too_small")
    require(0 <= int(settings.file_chunk_overlap_chars) < int(settings.file_chunk_chars), "file_chunk_overlap_invalid")
    require(int(settings.max_file_size_bytes) > 0, "file_size_limit_not_positive")
    require(int(settings.file_parse_timeout_seconds) > 0, "file_parse_timeout_not_positive")

    require(float(settings.project_runtime_default_cpu_limit) > 0, "runtime_default_cpu_not_positive")
    require(float(settings.project_runtime_default_cpu_limit) <= float(settings.project_runtime_max_cpu_limit), "runtime_default_cpu_exceeds_max")
    require(int(settings.project_runtime_default_memory_mb) <= int(settings.project_runtime_max_memory_mb), "runtime_default_memory_exceeds_max")
    require(int(settings.project_runtime_default_process_limit) <= int(settings.project_runtime_max_process_limit), "runtime_default_pids_exceeds_max")
    require(int(settings.sandbox_max_memory_mb) > 0 and float(settings.sandbox_max_cpu) > 0 and int(settings.sandbox_max_pids) > 0, "sandbox_envelope_not_positive")

    require(1 <= int(settings.image_default_steps) <= int(settings.image_max_steps), "image_default_steps_outside_max")
    require(int(settings.image_max_active_per_user) >= 1, "image_user_concurrency_not_positive")
    require(int(settings.image_max_dimension) > 0 and int(settings.image_max_pixels) > 0, "image_dimension_envelope_not_positive")
    require(int(settings.image_edit_max_source_dimension) > 0 and int(settings.image_edit_max_source_pixels) > 0, "image_edit_source_envelope_not_positive")
    require(0.0 < float(settings.image_edit_max_local_mask_ratio) <= 1.0, "image_edit_mask_ratio_invalid")
    require(0.0 <= float(settings.image_edit_min_plan_confidence) <= 1.0, "image_edit_plan_confidence_invalid")

    image_backend = str(settings.image_backend or "disabled").strip().lower()
    edit_backend = str(settings.image_edit_backend or "disabled").strip().lower()
    require(image_backend in {"disabled", "mock", "diffusers"}, "unsupported_image_generation_backend")
    require(edit_backend in SUPPORTED_EDIT_BACKENDS, "unsupported_image_edit_backend")

    # Image generation/edit checkpoints are paths on our own host. A URL/model
    # hub identifier must never become an implicit download/API escape hatch.
    image_paths = {
        "generation": str(settings.image_model_path or "").strip(),
        "edit": str(settings.image_edit_model_path or "").strip(),
        "identity": str(settings.image_edit_identity_model_path or "").strip(),
    }
    for label, value in image_paths.items():
        if value:
            require("://" not in value, f"image_{label}_model_path_must_be_local")
    if image_backend == "diffusers":
        require(bool(image_paths["generation"]), "image_generation_enabled_without_local_model_path")
    if edit_backend != "disabled":
        require(bool(image_paths["edit"] or image_paths["identity"]), "image_edit_enabled_without_model_path")
        if bool(settings.image_edit_require_vision_qa):
            require(bool(str(settings.image_vision_qa_url or "").strip()), "image_edit_requires_missing_vision_qa")

    vision_url = str(settings.image_vision_qa_url or "").strip()
    if vision_url:
        try:
            validate_vision_endpoint(
                vision_url,
                trusted_internal_base_url=str(settings.llama_base_url or ""),
            )
        except VisionEndpointError:
            errors.append("image_vision_endpoint_not_self_hosted")

    for name in ("chat", "research", "image", "sandbox"):
        active = int(getattr(settings, f"overload_{name}_max_active_http"))
        queue = int(getattr(settings, f"overload_{name}_max_queue"))
        timeout = float(getattr(settings, f"overload_{name}_queue_timeout_seconds"))
        require(active >= 1, f"overload_{name}_active_not_positive")
        require(queue >= 0, f"overload_{name}_queue_negative")
        require(timeout > 0, f"overload_{name}_timeout_not_positive")
    require(int(settings.overload_max_queued_per_principal) >= 1, "overload_principal_queue_not_positive")

    shares = {
        "fast": float(settings.plan_share_fast),
        "work": float(settings.plan_share_work),
        "deep": float(settings.plan_share_deep),
        "api": float(settings.plan_share_api),
        "image": float(settings.plan_share_image),
        "sandbox": float(settings.plan_share_sandbox),
    }
    require(all(0.0 <= value <= 1.0 for value in shares.values()), "plan_channel_share_out_of_range")
    require(abs(sum(shares.values()) - 1.0) <= 0.0001, "plan_channel_shares_do_not_sum_to_one")

    production = str(settings.env).lower() in {"production", "prod", "stable"}
    if production:
        require(not settings.is_sqlite, "production_database_is_sqlite")
        require(image_backend != "mock", "production_image_backend_is_mock")
        require(str(settings.document_render_backend).lower() == "remote", "production_document_renderer_not_isolated")
        require(str(settings.project_sandbox_backend).lower() == "remote", "production_sandbox_not_isolated")
        require(bool(str(settings.database_host or "").strip()), "production_database_host_missing")
        if vision_url:
            require(is_self_hosted_vision_origin(vision_url), "production_image_vision_not_self_hosted")

    return sorted(set(errors))


def main() -> int:
    settings = get_settings()
    errors = validate(settings)
    payload = {
        "format": "x1-runtime-config-audit-v2",
        "status": "passed" if not errors else "failed",
        "environment": str(settings.env),
        "server_profile": str(settings.server_optimization_profile),
        "image_compute_policy": "self_hosted_only",
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
