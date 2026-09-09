from __future__ import annotations

from pathlib import Path

from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint


SUPPORTED_EDIT_BACKENDS = {"disabled", "diffusers", "qwen-image-edit"}


def image_edit_capabilities(settings, *, worker_alive: bool | None = None) -> dict:
    backend = str(settings.image_edit_backend or "disabled").strip().lower()
    local_path = str(settings.image_edit_model_path or "").strip()
    identity_path = str(settings.image_edit_identity_model_path or "").strip()
    if backend == "qwen-image-edit" and not identity_path:
        identity_path = local_path

    vision_url = str(settings.image_vision_qa_url or "").strip()
    vision_valid = False
    vision_error = ""
    if vision_url:
        try:
            validate_vision_endpoint(vision_url, trusted_internal_base_url=str(settings.llama_base_url or ""))
            vision_valid = True
        except VisionEndpointError as exc:
            vision_error = str(exc)

    local_model = bool(local_path)
    identity_model = bool(identity_path)
    require_vision = bool(settings.image_edit_require_vision_qa)
    worker_ok = True if worker_alive is None else bool(worker_alive)
    reasons: list[str] = []
    if backend not in SUPPORTED_EDIT_BACKENDS:
        reasons.append("unsupported_image_edit_backend")
    elif backend == "disabled":
        reasons.append("image_edit_backend_not_configured")
    if not (local_model or identity_model):
        reasons.append("image_edit_model_not_configured")
    if require_vision and not vision_valid:
        reasons.append("vision_qa_not_ready")
    if worker_alive is not None and not worker_ok:
        reasons.append("image_worker_not_running")

    return {
        "backend": backend,
        "available": not reasons,
        "reasons": reasons,
        "local_object_edit": backend != "disabled" and local_model,
        "identity_recompose": backend != "disabled" and identity_model and vision_valid,
        "vision_ready": vision_valid,
        "vision_error": vision_error,
        "vision_url_kind": "configured_internal" if vision_valid else "disabled_or_invalid",
        "worker_alive": worker_ok,
        "local_model_path_configured": local_model,
        "identity_model_path_configured": identity_model,
        "identity_model_path": identity_path,
    }


def validate_image_edit_files(settings) -> list[str]:
    """Host/worker-side model presence checks without loading large weights."""
    caps = image_edit_capabilities(settings)
    if str(settings.image_edit_backend or "disabled").strip().lower() == "disabled":
        return []
    failures: list[str] = []
    for label, value in (
        ("local", str(settings.image_edit_model_path or "").strip()),
        ("identity", str(caps["identity_model_path"] or "").strip()),
    ):
        if not value:
            continue
        path = Path(value)
        if not path.is_dir():
            failures.append(f"{label}_image_edit_model_path_missing")
        elif not (path / "model_index.json").is_file():
            failures.append(f"{label}_image_edit_model_index_missing")
    return failures
