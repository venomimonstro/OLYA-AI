from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

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
from app.services.qwen_image_edit_backend import QwenImageEditBackend

ROOT = Path(__file__).resolve().parents[1]


def _png(size=(256, 192), color=(120, 130, 140), *, exif=False) -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    if exif:
        metadata = Image.Exif()
        metadata[274] = 1
        metadata[315] = "private-author"
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


def test_reference_normalization_strips_metadata_and_is_pixel_only_png():
    normalized = normalize_reference_image(
        _png(exif=True),
        kind="edit_source",
        max_dimension=4096,
        max_pixels=16 * 1024 * 1024,
    )
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
    for x in range(40, 80):
        for y in range(20, 60):
            mask.putpixel((x, y), 255)
    result = composite_preserving_outside(source, candidate, mask)
    for point in ((0, 0), (39, 20), (80, 20), (119, 79)):
        assert result.getpixel(point) == source.getpixel(point)
    assert result.getpixel((50, 30)) == candidate.getpixel((50, 30))
    assert outside_mask_change_score(source, result, mask) == 0.0


def test_outside_mask_change_detector_catches_one_pixel_drift():
    source = Image.new("RGB", (64, 64), "black")
    mask = Image.new("L", source.size, 0)
    mask.paste(255, (20, 20, 40, 40))
    result = composite_preserving_outside(source, Image.new("RGB", source.size, "white"), mask)
    result.putpixel((2, 2), (1, 0, 0))
    assert outside_mask_change_score(source, result, mask) > 0


def test_protected_box_is_removed_from_automatic_edit_mask():
    source = Image.new("RGB", (1000, 1000), "gray")
    plan = EditPlan(
        mode="remove_object",
        target="ship",
        target_boxes=((100, 100, 900, 900),),
        protected_boxes=((400, 400, 600, 600),),
        fill_prompt="background",
        confidence=0.99,
        planner="test",
    )
    mask = build_edit_mask(source, plan=plan, manual_mask=None, padding_ratio=0.0, feather_px=0)
    assert mask.getpixel((500, 500)) == 0
    assert mask.getpixel((200, 200)) == 255


def test_broad_auto_mask_is_measurable_for_runtime_rejection():
    source = Image.new("RGB", (1000, 1000), "gray")
    plan = EditPlan(
        mode="remove_object",
        target="oversized target",
        target_boxes=((50, 50, 950, 950),),
        protected_boxes=(),
        fill_prompt="background",
        confidence=0.99,
        planner="test",
    )
    mask = build_edit_mask(source, plan=plan, manual_mask=None, padding_ratio=0.0, feather_px=0)
    assert mask_coverage(mask) > 0.55


def test_manual_mask_must_match_source_dimensions():
    source = Image.new("RGB", (300, 200), "white")
    manual = Image.new("L", (200, 300), 255)
    plan = EditPlan("remove_object", "ship", (), (), "background", 1.0, "user-mask")
    with pytest.raises(ImageEditError, match="dimensions"):
        build_edit_mask(source, plan=plan, manual_mask=manual, padding_ratio=0.1, feather_px=0)


def test_image_edit_vision_is_loopback_only():
    with pytest.raises(ImageEditError, match="loopback"):
        LocalImageEditVision("https://example.com/vision")
    LocalImageEditVision("http://127.0.0.1:9999")


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


def test_durable_schema_and_migration_are_linear():
    migration = (ROOT / "alembic/versions/f59e8d4a2c10_add_image_editing.py").read_text("utf-8")
    assert 'revision = "f59e8d4a2c10"' in migration
    assert 'down_revision = "f53b21e7c4a0"' in migration
    assert '"image_references"' in migration
    assert '"image_edit_requests"' in migration
    registry = (ROOT / "app/models.py").read_text("utf-8")
    assert "models_sprint59" in registry


def test_http_admission_fails_closed_for_identity_and_strict_local_edits():
    source = (ROOT / "app/api/routes/image_editing.py").read_text("utf-8")
    for marker in (
        "Identity-preserving scene editing requires local vision QA",
        "Strict local object editing requires preserve_outside_mask=true",
        "Automatic edit intent/object localization requires local vision QA",
        "Project-scoped identity reference belongs to another project",
    ):
        assert marker in source


def test_worker_handles_generation_and_edit_as_distinct_durable_jobs():
    source = (ROOT / "scripts/image_worker.py").read_text("utf-8")
    assert 'kinds={"image.generate", "image.edit"}' in source
    assert 'if job.kind == "image.edit"' in source
    assert "execute_edit_generation" in source
    assert "QwenImageEditBackend" in source
    assert "quality-gate rejection is a completed durable job" in source


def test_edit_runtime_never_delivers_identity_recompose_without_vision():
    source = (ROOT / "app/services/image_edit_runtime.py").read_text("utf-8")
    assert 'plan.mode == "identity_recompose" and edit.preserve_identity and vision is None' in source
    assert "Identity-preserving scene editing requires local semantic vision QA" in source
    assert 'generation.qa_status = "passed"' in source
    assert 'generation.qa_status = "failed"' in source


def test_studio_shows_only_qa_passed_result_and_uses_authenticated_blob_fetch():
    source = (ROOT / "app/api/routes/image_editing.py").read_text("utf-8")
    assert '@router.get("/studio"' in source
    assert "Здесь появится только изображение, прошедшее QA" in source
    assert "Authorization:'Bearer '+token" in source
    assert "edit.status==='failed'" in source
    assert "generationId+'/content?variant=preferred'" in source
