#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint

ROOT = Path(__file__).resolve().parents[1]


def _service_section(text: str, service: str) -> str:
    marker = f"\n  {service}:\n"
    if text.startswith(f"services:\n  {service}:\n"):
        rest = text.split(f"services:\n  {service}:\n", 1)[1]
    elif marker in text:
        rest = text.split(marker, 1)[1]
    else:
        return ""
    match = re.search(r"\n  [A-Za-z0-9_.-]+:\n", rest)
    return rest[: match.start()] if match else rest


def _rejects_public(url: str) -> bool:
    try:
        validate_vision_endpoint(url, trusted_internal_base_url=url)
    except VisionEndpointError:
        return True
    return False


def audit() -> dict:
    errors: list[dict] = []

    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    worker = _service_section(compose, "image-worker")
    database = _service_section(compose, "db")
    llama = _service_section(compose, "llama")
    networks_tail = compose.split("\nnetworks:\n", 1)[1] if "\nnetworks:\n" in compose else ""

    def require(condition: bool, code: str, detail: str = "") -> None:
        if not condition:
            errors.append({"code": code, "detail": detail[:500]})

    require(bool(worker), "image_worker_service_missing")
    require("image-private" in networks_tail and "internal: true" in networks_tail, "image_private_network_not_internal")
    require("networks:\n      - image-private" in worker, "image_worker_not_on_private_network")
    require("- default" not in worker, "image_worker_has_default_network")
    require("image-private" in database, "database_missing_image_private_network")
    require("image-private" in llama, "llama_missing_image_private_network")
    require("./models:/models:ro" in worker, "image_worker_models_not_read_only")
    require("./data:/app/data" in worker, "image_worker_data_volume_missing")

    qwen = (ROOT / "app/services/qwen_image_edit_backend.py").read_text("utf-8")
    vision = (ROOT / "app/services/image_vision.py").read_text("utf-8")
    worker_source = (ROOT / "scripts/image_worker.py").read_text("utf-8")
    config = (ROOT / "app/core/config.py").read_text("utf-8")

    require('local_files_only": True' in qwen or "local_files_only': True" in qwen, "qwen_edit_not_local_files_only")
    require("trust_env=False" in vision, "image_vision_honors_environment_proxy")
    require("LocalDiffusersBackend" in worker_source and "QwenImageEditBackend" in worker_source, "local_image_backends_missing")
    for forbidden in (
        "OpenAIImage", "Replicate", "StabilityAI", "FalClient", "TogetherImage",
        "api.openai.com", "api.replicate.com", "api.stability.ai", "fal.ai/v1",
    ):
        require(forbidden not in worker_source, "external_image_provider_reference", forbidden)

    # Configuration exposes local checkpoint paths, not provider API keys.
    for forbidden_setting in (
        "image_openai_api_key", "image_replicate_api_key", "image_stability_api_key",
        "image_fal_api_key", "image_provider_url",
    ):
        require(forbidden_setting not in config.lower(), "external_image_setting_present", forbidden_setting)

    accepted: list[str] = []
    for value in ("http://127.0.0.1:8080", "http://llama:8080", "http://10.42.0.8:8080"):
        try:
            validate_vision_endpoint(value, trusted_internal_base_url="http://llama:8080")
            accepted.append(value)
        except VisionEndpointError:
            errors.append({"code": "self_hosted_vision_origin_rejected", "detail": value})

    rejected: list[str] = []
    for value in (
        "https://api.openai.com",
        "https://example.com:8080",
        "http://8.8.8.8:8080",
        "https://vision.vendor.example/v1",
    ):
        if _rejects_public(value):
            rejected.append(value)
        else:
            errors.append({"code": "public_vision_origin_accepted", "detail": value})

    return {
        "format": "x1-local-image-contract-v1",
        "status": "passed" if not errors else "failed",
        "policy": "image_generation_editing_and_vision_are_self_hosted_only",
        "runtime_network": "image-private/internal",
        "accepted_self_hosted_origins": accepted,
        "rejected_public_origin_samples": rejected,
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
