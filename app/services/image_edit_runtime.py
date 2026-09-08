from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ImageBlob, ImageEditRequest, ImageGeneration, ImageQAEvent, ImageReference, ImageSafetyPolicy, ImageVariant, utcnow
from app.services.image_editing import (
    DiffusersImageEditBackend,
    EditPlan,
    ImageEditError,
    LocalImageEditVision,
    build_backend_prompt,
    build_edit_mask,
    composite_preserving_outside,
    mask_coverage,
    outside_mask_change_score,
    resolve_edit_mode,
)
from app.services.image_policy import compose_effective_prompt, compose_negative_prompt
from app.services.image_references import load_reference_image


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _webp_bytes(image: Image.Image, max_side: int) -> tuple[bytes, int, int]:
    preview = image.convert("RGB").copy()
    preview.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, "WEBP", quality=88, method=6)
    return buffer.getvalue(), preview.width, preview.height


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _store_blob(db: Session, *, storage_root: str, content: bytes, media_type: str, width: int, height: int, namespace: str) -> ImageBlob:
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == digest))
    if existing is not None:
        if not Path(existing.storage_path).is_file():
            raise ImageEditError("Deduplicated image blob is missing from storage")
        return existing
    root = Path(storage_root).resolve()
    suffix = ".webp" if media_type == "image/webp" else ".png"
    path = root / namespace / digest[:2] / f"{digest}{suffix}"
    _atomic_write(path, content)
    row = ImageBlob(sha256=digest, media_type=media_type, width=width, height=height, size_bytes=len(content), storage_path=str(path))
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        winner = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == digest))
        if winner is None:
            raise
        return winner


def _qa_event(db: Session, generation: ImageGeneration, *, attempt: int, qa_type: str, passed: bool, findings: list[dict], metrics: dict) -> None:
    db.add(ImageQAEvent(
        generation_id=generation.id,
        attempt=attempt,
        status="passed" if passed else "failed",
        findings=findings[:20],
        metrics=metrics,
        qa_type=qa_type,
    ))


def _resolve_plan(
    *,
    source: Image.Image,
    edit: ImageEditRequest,
    manual_mask: Image.Image | None,
    vision: LocalImageEditVision | None,
    min_confidence: float,
) -> EditPlan:
    requested_mode = resolve_edit_mode(edit.mode, edit.instruction)
    if requested_mode == "identity_recompose":
        return EditPlan(
            mode="identity_recompose", target="person/scene", target_boxes=(), protected_boxes=(),
            fill_prompt=edit.instruction, confidence=1.0, planner="deterministic-intent",
        )
    if manual_mask is not None and requested_mode != "auto":
        return EditPlan(
            mode=requested_mode, target="manual-mask", target_boxes=(), protected_boxes=(),
            fill_prompt=edit.instruction, confidence=1.0, planner="user-mask",
        )
    if vision is None:
        raise ImageEditError("Automatic object localization requires local vision QA or a user-supplied mask")
    planned = vision.plan(source, instruction=edit.instruction, requested_mode=requested_mode)
    if planned.mode == "unknown" or planned.confidence < min_confidence:
        raise ImageEditError("Edit target could not be located with sufficient confidence")
    if requested_mode != "auto" and planned.mode != requested_mode:
        # Never broaden/narrow a user-selected operation silently. The model can
        # locate the target, but the server remains the authority on edit mode.
        planned = EditPlan(
            mode=requested_mode,
            target=planned.target,
            target_boxes=planned.target_boxes,
            protected_boxes=planned.protected_boxes,
            fill_prompt=planned.fill_prompt,
            confidence=planned.confidence,
            planner=planned.planner,
        )
    return planned


def _mark_failed(generation: ImageGeneration, edit: ImageEditRequest, reason: str, *, qa_summary: dict | None = None) -> dict:
    generation.status = "failed"
    generation.qa_status = "failed"
    generation.error_message = reason[:1600]
    generation.finished_at = utcnow()
    edit.status = "failed"
    edit.error_message = reason[:1600]
    edit.qa_summary = qa_summary or {"passed": False, "reason": reason[:500]}
    edit.updated_at = utcnow()
    return {"delivered": False, "reason": reason[:500]}


def execute_edit_generation(
    db: Session,
    generation: ImageGeneration,
    *,
    backend: DiffusersImageEditBackend,
    storage_root: str,
    vision: LocalImageEditVision | None,
    qa_max_repairs: int,
    preview_max_side: int,
    mask_padding_ratio: float,
    mask_feather_px: int,
    max_local_mask_ratio: float,
    min_plan_confidence: float,
    require_vision_qa: bool,
) -> dict:
    edit = db.scalar(select(ImageEditRequest).where(ImageEditRequest.generation_id == generation.id))
    if edit is None:
        raise ImageEditError("Image edit request is missing")
    source_ref = db.get(ImageReference, edit.source_reference_id)
    if source_ref is None:
        return _mark_failed(generation, edit, "Source image reference is missing")
    try:
        _source_blob, source = load_reference_image(db, source_ref)
        manual_mask = None
        if edit.mask_reference_id:
            mask_ref = db.get(ImageReference, edit.mask_reference_id)
            if mask_ref is None or mask_ref.kind != "mask":
                raise ImageEditError("Edit mask reference is invalid")
            _mask_blob, manual_mask = load_reference_image(db, mask_ref)
        identity_source = source
        if edit.identity_reference_id:
            identity_ref = db.get(ImageReference, edit.identity_reference_id)
            if identity_ref is None or identity_ref.kind not in {"identity", "edit_source"}:
                raise ImageEditError("Identity reference is invalid")
            _identity_blob, identity_source = load_reference_image(db, identity_ref)

        generation.status = "generating"
        generation.started_at = generation.started_at or utcnow()
        edit.status = "planning"
        edit.updated_at = utcnow()
        db.flush()

        plan = _resolve_plan(
            source=source,
            edit=edit,
            manual_mask=manual_mask,
            vision=vision,
            min_confidence=min_plan_confidence,
        )
        edit.plan = plan.as_dict()
        edit.status = "generating"
        edit.updated_at = utcnow()

        mask = None
        coverage = 1.0
        if plan.mode != "identity_recompose":
            mask = build_edit_mask(
                source,
                plan=plan,
                manual_mask=manual_mask,
                padding_ratio=mask_padding_ratio,
                feather_px=mask_feather_px,
            )
            coverage = mask_coverage(mask)
            if plan.mode != "background" and coverage > max_local_mask_ratio:
                raise ImageEditError("Automatic edit mask is too broad; refusing to rewrite most of the photograph")
            if coverage < 0.0002:
                raise ImageEditError("Edit mask is too small to produce a reliable change")

        policy = db.get(ImageSafetyPolicy, generation.safety_policy_id) if generation.safety_policy_id else None
        max_attempts = 1 + max(0, min(3, int(qa_max_repairs)))
        last_semantic: dict = {}
        last_findings: list[dict] = []
        passed = False
        final_image: Image.Image | None = None

        for attempt in range(1, max_attempts + 1):
            positive, negative = build_backend_prompt(edit.instruction, plan, repair_findings=last_findings if attempt > 1 else None)
            positive = compose_effective_prompt(policy, positive)
            negative = compose_negative_prompt(policy, negative)
            attempt_seed = (int(generation.seed) + (attempt - 1) * 7919) % (2**31)
            if plan.mode == "identity_recompose":
                if edit.strict_quality and require_vision_qa and vision is None:
                    raise ImageEditError("Strict identity editing requires local semantic vision QA")
                candidate = backend.edit_identity(
                    source=identity_source,
                    prompt=positive,
                    negative_prompt=negative,
                    steps=generation.steps,
                    seed=attempt_seed,
                ).resize(source.size, Image.Resampling.LANCZOS)
                deterministic_passed = candidate.size == source.size
                outside_score = None
            else:
                assert mask is not None
                candidate = backend.edit_local(
                    source=source,
                    mask=mask,
                    prompt=positive,
                    negative_prompt=negative,
                    steps=generation.steps,
                    seed=attempt_seed,
                )
                if edit.preserve_outside_mask:
                    candidate = composite_preserving_outside(source, candidate, mask)
                candidate = candidate.resize(source.size, Image.Resampling.LANCZOS)
                outside_score = outside_mask_change_score(source, candidate, mask)
                deterministic_passed = candidate.size == source.size and (outside_score == 0.0 if edit.preserve_outside_mask else True)

            extrema = candidate.convert("L").getextrema()
            if extrema[0] == extrema[1]:
                deterministic_passed = False
                last_findings = [{"code": "degenerate_image", "severity": "critical", "repairable": True, "detail": "Generated edit is visually degenerate"}]
            _qa_event(
                db,
                generation,
                attempt=attempt,
                qa_type="edit_deterministic",
                passed=deterministic_passed,
                findings=[] if deterministic_passed else last_findings,
                metrics={"mask_coverage": round(coverage, 6), "outside_mask_max_channel_delta": outside_score},
            )
            if not deterministic_passed:
                continue

            if vision is not None:
                semantic = vision.verify(
                    source,
                    candidate,
                    instruction=edit.instruction,
                    mode=plan.mode,
                    preserve_identity=edit.preserve_identity,
                )
                last_semantic = semantic
                last_findings = list(semantic.get("findings") or [])
                _qa_event(
                    db,
                    generation,
                    attempt=attempt,
                    qa_type="edit_semantic",
                    passed=bool(semantic.get("passed")),
                    findings=last_findings,
                    metrics={
                        "instruction_satisfied": bool(semantic.get("instruction_satisfied")),
                        "identity_consistent": bool(semantic.get("identity_consistent")),
                        "unintended_changes": bool(semantic.get("unintended_changes")),
                    },
                )
                if semantic.get("passed"):
                    passed = True
                    final_image = candidate
                    break
            elif edit.strict_quality and require_vision_qa:
                last_findings = [{"code": "semantic_qa_unavailable", "severity": "critical", "repairable": False, "detail": "Strict edit cannot verify instruction success"}]
                _qa_event(db, generation, attempt=attempt, qa_type="edit_semantic", passed=False, findings=last_findings, metrics={"local_vision_qa": False})
                break
            else:
                passed = True
                final_image = candidate
                last_semantic = {"passed": True, "semantic_check": "not_required_by_request"}
                break

        generation.repair_attempts = max(0, (attempt if "attempt" in locals() else 1) - 1)
        qa_summary = {
            "passed": passed,
            "attempts": int(attempt if "attempt" in locals() else 0),
            "mode": plan.mode,
            "planner": plan.planner,
            "planner_confidence": plan.confidence,
            "mask_coverage": round(coverage, 6),
            "semantic": last_semantic,
        }
        edit.qa_summary = qa_summary
        edit.updated_at = utcnow()
        if not passed or final_image is None:
            return _mark_failed(generation, edit, "Image edit did not satisfy strict quality gates", qa_summary=qa_summary)

        content = _png_bytes(final_image)
        blob = _store_blob(
            db,
            storage_root=storage_root,
            content=content,
            media_type="image/png",
            width=final_image.width,
            height=final_image.height,
            namespace=f"edits/{generation.id}",
        )
        preview_content, preview_w, preview_h = _webp_bytes(final_image, preview_max_side)
        preview_blob = _store_blob(
            db,
            storage_root=storage_root,
            content=preview_content,
            media_type="image/webp",
            width=preview_w,
            height=preview_h,
            namespace=f"previews/{generation.id}",
        )
        existing_variant = db.scalar(select(ImageVariant).where(ImageVariant.source_blob_id == blob.id, ImageVariant.kind == "preview_webp"))
        if existing_variant is None:
            db.add(ImageVariant(source_blob_id=blob.id, blob_id=preview_blob.id, kind="preview_webp", codec="webp", quality=88, perceptual_error=None))

        generation.blob_id = blob.id
        generation.preferred_blob_id = blob.id
        generation.status = "ready"
        generation.qa_status = "passed"
        generation.error_message = ""
        generation.finished_at = utcnow()
        generation.manifest = {
            **(generation.manifest or {}),
            "operation": "image_edit",
            "edit_request_id": edit.id,
            "source_reference_id": edit.source_reference_id,
            "mask_reference_id": edit.mask_reference_id,
            "identity_reference_id": edit.identity_reference_id,
            "edit_plan": plan.as_dict(),
            "qa": qa_summary,
            "result_sha256": blob.sha256,
            "pixel_preservation": "outside-mask pixels restored server-side" if plan.mode != "identity_recompose" and edit.preserve_outside_mask else "semantic identity/scene verification",
        }
        edit.status = "ready"
        edit.error_message = ""
        edit.updated_at = utcnow()
        db.flush()
        return {"delivered": True, "generation_id": generation.id, "blob_id": blob.id, "edit_request_id": edit.id, "qa": qa_summary}
    except ImageEditError as exc:
        return _mark_failed(generation, edit, str(exc))
