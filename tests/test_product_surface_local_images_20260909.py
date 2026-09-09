from pathlib import Path

import pytest

from app.core.config import Settings
from app.main import app
from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint
from scripts.local_image_contract_audit import audit as audit_local_images
from scripts.product_surface_audit import audit as audit_product_surface
from scripts.runtime_config_audit import validate as validate_runtime_config

ROOT = Path(__file__).resolve().parents[1]


def test_image_vision_rejects_public_origin_even_when_marked_trusted():
    for url in ("https://api.openai.com", "https://example.com:8080", "http://8.8.8.8:8080"):
        with pytest.raises(VisionEndpointError):
            validate_vision_endpoint(url, trusted_internal_base_url=url)


def test_image_vision_accepts_only_self_hosted_examples():
    assert validate_vision_endpoint("http://127.0.0.1:8080").source == "loopback"
    assert validate_vision_endpoint("http://llama:8080").source == "internal_service"
    assert validate_vision_endpoint("http://10.42.0.8:8080").source == "private_network"


def test_runtime_config_rejects_external_image_model_url_and_external_vision():
    base = Settings()
    bad_model = base.model_copy(update={"image_backend": "diffusers", "image_model_path": "https://models.example/image"})
    assert "image_generation_model_path_must_be_local" in validate_runtime_config(bad_model)

    bad_vision = base.model_copy(update={"image_vision_qa_url": "https://api.example.com/v1"})
    assert "image_vision_endpoint_not_self_hosted" in validate_runtime_config(bad_vision)


def test_image_worker_runtime_isolated_from_public_network():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "image-private:\n    internal: true" in compose
    worker = compose.split("\n  image-worker:\n", 1)[1].split("\n  llama:\n", 1)[0]
    assert "networks:\n      - image-private" in worker
    assert "- default" not in worker
    assert "./models:/models:ro" in worker


def test_qwen_image_edit_is_local_files_only():
    source = (ROOT / "app/services/qwen_image_edit_backend.py").read_text("utf-8")
    assert '"local_files_only": True' in source


def test_image_studio_is_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/studio" in paths
    assert "/v1/images/references" in paths
    assert "/v1/images/edits" in paths
    assert "/v1/images/generations/{generation_id}/content" in paths


def test_product_surface_contract_has_no_missing_registration_or_duplicate_routes():
    report = audit_product_surface()
    assert report["status"] == "passed", report["errors"]


def test_local_image_contract_is_release_clean():
    report = audit_local_images()
    assert report["status"] == "passed", report["errors"]
