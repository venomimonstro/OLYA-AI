from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from app.schemas.image_editing import ImageEditCreate
from app.services.image_editing import (
    EditPlan,
    ImageEditError,
    LocalImageEditVision,
    build_edit_mask,
    composite_preserving_outside,
    infer_edit_mode,
    mask_coverage,
    outside_mask_change_score,
    resolve_edit_mode,
)
from app.services.image_references import ImageReferenceError, normalize_reference_image
from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint
from app.services.qwen_image_edit_backend import QwenImageEditBackend

ROOT = Path(__file__).resolve().parents[1]


def _png(size=(256, 192), color=(120, 130, 140), *, exif=False) -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    if exif:
        metadata = Image.Exif(); metadata[274] = 1; metadata[315] = "private-author"
        image.save(output, "JPEG", exif=metadata)
    else:
        image.save(output, "PNG")
    return output.getvalue()


def test_russian_edit_intent_routes_remove_and_identity_recompose():
    assert infer_edit_mode("убери корабль на заднем фоне") == "remove_object"
    assert infer_edit_mode("Вот фото, сделай меня за рулём автомобиля") == "identity_recompose"
    assert infer_edit_mode("Добавь красную чашку на стол") == "add_object"
    assert infer_edit_mode("Замени синюю машину на белую") == "replace_object"


def test_explicit_local_mode_cannot_hide_identity_scene_rewrite():
    with pytest.raises(ImageEditError, match="identity_recompose"):
        resolve_edit_mode("remove_object", "сделай меня за рулём автомобиля")


def test_identity_contract_cannot_disable_strict_or_identity_in_auto_mode():
    with pytest.raises(ValidationError):
        ImageEditCreate(source_reference_id="x", instruction="сделай меня за рулём автомобиля", strict_quality=False)
    with pytest.raises(ValidationError):
        ImageEditCreate(source_reference_id="x", instruction="сделай меня за рулём автомобиля", preserve_identity=False)


def test_reference_normalization_strips_metadata_and_is_pixel_only_png():
    normalized = normalize_reference_image(_png(exif=True), kind="edit_source", max_dimension=4096, max_pixels=16 * 1024 * 1024)
    assert normalized.media_type == "image/png"
    result = Image.open(io.BytesIO(normalized.content))
    assert result.format == "PNG"
    assert not result.getexif()
    assert "private-author" not in normalized.content.decode("latin1", errors="ignore")


def test_reference_parser_rejects_tiny_and_oversized_images():
    with pytest.raises(ImageReferenceError, match="too small"):
        normalize_reference_image(_png(size=(16, 16)), kind="edit_source", max_dimension=4096, max_pixels=20_000_000)
    with pytest.raises(ImageReferenceError, match="dimensions"):
        normalize_reference_image(_png(size=(300, 300)), kind="edit_source", max_dimension=256, max_pixels=20_000_000)


def test_mask_upload_is_binary_after_normalization():
    image = Image.new("L", (128, 128), 100)
    for x in range(64, 128):
        for y in range(128):
            image.putpixel((x, y), 200)
    buf = io.BytesIO(); image.save(buf, "PNG")
    normalized = normalize_reference_image(buf.getvalue(), kind="mask", max_dimension=512, max_pixels=1_000_000)
    mask = Image.open(io.BytesIO(normalized.content)).convert("L")
    assert set(mask.getdata()) == {0, 255}


def test_local_edit_restores_every_exact_pixel_outside_mask():
    source = Image.new("RGB", (120, 80), (10, 20, 30))
    candidate = Image.new("RGB", source.size, (220, 40, 80))
    mask = Image.new("L", source.size, 0)
    mask.paste(255, (40, 20, 80, 60))
    result = composite_preserving_outside(source, candidate, mask)
    for point in ((0, 0), (39, 20), (80, 20), (119, 79)):
        assert result.getpixel(point) == source.getpixel(point)
    assert result.getpixel((50, 30)) == candidate.getpixel((50, 30))
    assert outside_mask_change_score(source, result, mask) == 0.0


def test_outside_mask_change_detector_catches_one_pixel_drift():
    source = Image.new("RGB", (64, 64), "black")
    mask = Image.new("L", source.size, 0); mask.paste(255, (20, 20, 40, 40))
    result = composite_preserving_outside(source, Image.new("RGB", source.size, "white"), mask)
    result.putpixel((2, 2), (1, 0, 0))
    assert outside_mask_change_score(source, result, mask) > 0


def test_protected_box_is_removed_from_automatic_edit_mask():
    source = Image.new("RGB", (1000, 1000), "gray")
    plan = EditPlan("remove_object", "ship", ((100, 100, 900, 900),), ((400, 400, 600, 600),), "background", 0.99, "test")
    mask = build_edit_mask(source, plan=plan, manual_mask=None, padding_ratio=0.0, feather_px=0)
    assert mask.getpixel((500, 500)) == 0
    assert mask.getpixel((200, 200)) == 255


def test_broad_auto_mask_is_measurable_for_runtime_rejection():
    source = Image.new("RGB", (1000, 1000), "gray")
    plan = EditPlan("remove_object", "oversized", ((50, 50, 950, 950),), (), "background", 0.99, "test")
    mask = build_edit_mask(source, plan=plan, manual_mask=None, padding_ratio=0.0, feather_px=0)
    assert mask_coverage(mask) > 0.55


def test_manual_mask_must_match_source_dimensions():
    source = Image.new("RGB", (300, 200), "white")
    manual = Image.new("L", (200, 300), 255)
    plan = EditPlan("remove_object", "ship", (), (), "background", 1.0, "user-mask")
    with pytest.raises(ImageEditError, match="dimensions"):
        build_edit_mask(source, plan=plan, manual_mask=manual, padding_ratio=0.1, feather_px=0)


def test_private_vision_endpoint_allows_only_loopback_or_exact_llama_origin():
    with pytest.raises(VisionEndpointError):
        validate_vision_endpoint("https://example.com/vision", trusted_internal_base_url="http://llama:8080")
    assert validate_vision_endpoint("http://127.0.0.1:9999").source == "loopback"
    endpoint = validate_vision_endpoint("http://llama:8080", trusted_internal_base_url="http://llama:8080")
    assert endpoint.source == "trusted_llama"
    LocalImageEditVision("http://llama:8080", trusted_internal_base_url="http://llama:8080", model_name="Qwen3.6-35B-A3B-Q4_K_M")


def test_qwen_backend_refuses_non_qwen_checkpoint(tmp_path: Path):
    (tmp_path / "model_index.json").write_text(json.dumps({"_class_name": "FluxKontextPipeline"}), "utf-8")
    backend = QwenImageEditBackend(model_path=str(tmp_path))
    with pytest.raises(ImageEditError, match="not a Qwen Image Edit"):
        backend._validate_path(str(tmp_path))


def test_qwen_backend_accepts_qwen_plus_contract_without_loading_weights(tmp_path: Path):
    (tmp_path / "model_index.json").write_text(json.dumps({"_class_name": "QwenImageEditPlusPipeline"}), "utf-8")
    backend = QwenImageEditBackend(model_path=str(tmp_path))
    path, class_name = backend._validate_path(str(tmp_path))
    assert path == tmp_path
    assert class_name == "QwenImageEditPlusPipeline"


def test_migrations_keep_single_linear_image_edit_head():
    f59 = (ROOT / "alembic/versions/f59e8d4a2c10_add_image_editing.py").read_text("utf-8")
    f60 = (ROOT / "alembic/versions/f60a93c7d511_image_reference_privacy_cleanup.py").read_text("utf-8")
    assert 'down_revision = "f53b21e7c4a0"' in f59
    assert 'down_revision = "f59e8d4a2c10"' in f60
    assert 'alter_column("blob_id"' in f60
    assert "models_sprint59" in (ROOT / "app/models.py").read_text("utf-8")


def test_http_admission_uses_live_worker_and_central_capability_contract():
    source = (ROOT / "app/api/routes/image_editing.py").read_text("utf-8")
    for marker in (
        "image_worker_snapshot",
        "image_edit_capabilities",
        "Image worker is not running",
        "Identity-preserving scene editing is not configured",
        "Strict local object editing requires preserve_outside_mask=true",
    ):
        assert marker in source


def test_worker_handles_generation_and_edit_with_preflight_and_heartbeat():
    source = (ROOT / "scripts/image_worker.py").read_text("utf-8")
    assert 'kinds={"image.generate", "image.edit"}' in source
    assert 'if job.kind == "image.edit"' in source
    assert "execute_edit_generation" in source
    assert "QwenImageEditBackend" in source
    assert "write_image_worker_heartbeat" in source
    assert "preflight" in source


def test_edit_runtime_never_delivers_identity_or_policy_qa_without_vision():
    source = (ROOT / "app/services/image_edit_runtime.py").read_text("utf-8")
    assert 'plan.mode == "identity_recompose" and vision is None' in source
    assert "Identity-preserving scene editing requires local semantic vision QA" in source
    assert "Image editing policy requires local semantic vision QA" in source
    assert 'generation.qa_status = "passed"' in source
    assert 'generation.qa_status = "failed"' in source


def test_identity_backend_receives_separate_scene_and_identity_reference():
    source = (ROOT / "app/services/image_edit_runtime.py").read_text("utf-8")
    assert '"source": scene' in source
    assert '"identity_reference": identity_reference' in source
    qwen = (ROOT / "app/services/qwen_image_edit_backend.py").read_text("utf-8")
    assert "image_input = [identity, scene]" in qwen
    assert "does not support multi-image identity + scene editing" in qwen


def test_studio_shows_only_qa_passed_result_and_never_exposes_optional_strict_toggle():
    source = (ROOT / "app/image_studio_ui.py").read_text("utf-8")
    assert "Здесь появится только QA-прошедший результат" in source
    assert "Authorization:'Bearer '+token" in source
    assert "edit.status==='failed'" in source
    assert "strict_quality:true" in source
    assert 'type="checkbox"' not in source


def test_reference_deletion_detaches_bytes_and_content_is_streamed():
    service = (ROOT / "app/services/image_references.py").read_text("utf-8")
    route = (ROOT / "app/api/routes/image_editing.py").read_text("utf-8")
    assert "delete_reference_bytes" in service
    assert "reference.blob_id = None" in service
    assert "db.delete(blob)" in service
    assert "unlink_after_commit" in route
    assert "FileResponse" in route


def test_qwen_multimodal_projector_is_pinned_and_served_by_llama_cpp():
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    projector = manifest["vision_projector"]
    assert projector["filename"] == "mmproj-Qwen3.6-35B-A3B-Q8_0.gguf"
    assert len(projector["sha256"]) == 64
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "--mmproj" in compose
    assert "X1_LLAMA_MMPROJ_FILE" in compose


def test_qwen_gpu_worker_requires_explicit_nvidia_override_and_accelerate():
    gpu = (ROOT / "docker-compose.image-gpu.yml").read_text("utf-8")
    manager = (ROOT / "scripts/manage_image_worker.py").read_text("utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert "runtime: nvidia" in gpu
    assert "NVIDIA_VISIBLE_DEVICES" in gpu
    assert "NVIDIA Container Runtime" in manager
    assert "accelerate" in pyproject


def test_image_post_surfaces_share_bounded_overload_lane():
    main = (ROOT / "app/main.py").read_text("utf-8")
    assert '"/v1/images/generations", "/v1/images/edits", "/v1/images/references"' in main
