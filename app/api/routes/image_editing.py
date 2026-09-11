from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BackgroundJob, ImageBlob, ImageEditRequest, ImageGeneration, ImageReference, User, utcnow
from app.schemas.image_editing import ImageEditCreate, ImageEditCreateResponse, ImageEditRead, ImageReferenceRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.image_capabilities import image_edit_capabilities
from app.services.image_editing import ImageEditError, resolve_edit_mode
from app.services.image_policy import evaluate_prompt, published_policy
from app.services.image_references import ImageReferenceError, delete_reference_bytes, store_reference, total_user_image_storage_bytes
from app.services.image_runtime import ImageRuntimeError, ensure_disk_capacity
from app.services.image_worker_state import image_worker_snapshot
from app.services.jobs import enqueue_job
from app.services.safety import require_capability

router = APIRouter(prefix="/v1/images", tags=["image-editing"])
_LOCAL_EDIT_MODES = {"remove_object", "replace_object", "add_object", "background"}


def _reference_access(db: Session, user: User, reference_id: str, minimum_project_role: str = "viewer") -> ImageReference:
    row = db.get(ImageReference, reference_id)
    if row is None or row.status != "ready" or not row.blob_id:
        raise HTTPException(status_code=404, detail="Image reference not found")
    if row.project_id:
        require_project_role(db, user, row.project_id, minimum_project_role)
    elif row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Image reference not found")
    return row


def _edit_access(db: Session, user: User, edit_id: str) -> ImageEditRequest:
    row = db.get(ImageEditRequest, edit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Image edit not found")
    if row.project_id:
        require_project_role(db, user, row.project_id, "viewer")
    elif row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Image edit not found")
    return row


def _safe_blob_path(blob: ImageBlob, storage_root: str) -> Path:
    root = Path(storage_root).resolve()
    path = Path(blob.storage_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Image blob path is outside configured storage") from exc
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Image content is unavailable")
    return path


async def _read_limited_image(request: Request, limit: int) -> bytes:
    declared_values = request.headers.get_list("content-length")
    if declared_values:
        normalized = {value.strip() for item in declared_values for value in item.split(",") if value.strip()}
        if len(normalized) != 1:
            raise HTTPException(status_code=400, detail="Conflicting Content-Length")
        try:
            size = int(next(iter(normalized)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if size > limit:
            raise HTTPException(status_code=413, detail="Reference image is too large")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="Reference image is too large")
        content.extend(chunk)
    if not content:
        raise HTTPException(status_code=400, detail="Reference image is empty")
    return bytes(content)


@router.post("/references", response_model=ImageReferenceRead, status_code=status.HTTP_201_CREATED)
async def upload_reference(
    request: Request,
    filename: str = Query(default="image.png", min_length=1, max_length=240),
    kind: Literal["edit_source", "identity", "mask"] = Query(default="edit_source"),
    project_id: str | None = Query(default=None),
    source_reference_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImageReference:
    require_capability(db, user.id, "images")
    if project_id:
        require_project_role(db, user, project_id, "member")
    source = None
    if kind == "mask":
        if not source_reference_id:
            raise HTTPException(status_code=422, detail="A mask must declare source_reference_id")
        source = _reference_access(db, user, source_reference_id, "viewer")
        if project_id and source.project_id and project_id != source.project_id:
            raise HTTPException(status_code=409, detail="Mask and source reference belong to different projects")
    data = await _read_limited_image(request, int(request.app.state.settings.image_edit_max_source_bytes))
    try:
        reference = store_reference(
            db,
            user=user,
            project_id=project_id or (source.project_id if source else None),
            filename=filename,
            kind=kind,
            data=data,
            settings=request.app.state.settings,
            source_reference_id=source_reference_id,
        )
    except (ImageReferenceError, ImageRuntimeError) as exc:
        db.rollback()
        message = str(exc)
        code = 507 if any(word in message.lower() for word in ("quota", "disk", "storage")) else 422
        raise HTTPException(status_code=code, detail=message) from exc
    db.commit()
    db.refresh(reference)
    return reference


@router.get("/references", response_model=list[ImageReferenceRead])
def list_references(
    project_id: str | None = None,
    kind: Literal["edit_source", "identity", "mask"] | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ImageReference]:
    if project_id:
        require_project_role(db, user, project_id, "viewer")
        stmt = select(ImageReference).where(ImageReference.project_id == project_id, ImageReference.status == "ready")
    else:
        stmt = select(ImageReference).where(ImageReference.user_id == user.id, ImageReference.status == "ready")
    if kind:
        stmt = stmt.where(ImageReference.kind == kind)
    return list(db.scalars(stmt.order_by(ImageReference.created_at.desc()).limit(200)).all())


@router.get("/references/{reference_id}", response_model=ImageReferenceRead)
def get_reference(reference_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ImageReference:
    return _reference_access(db, user, reference_id)


@router.get("/references/{reference_id}/content")
def reference_content(reference_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    reference = _reference_access(db, user, reference_id)
    blob = db.get(ImageBlob, reference.blob_id)
    if blob is None:
        raise HTTPException(status_code=410, detail="Reference image blob is unavailable")
    path = _safe_blob_path(blob, request.app.state.settings.image_storage_path)
    return FileResponse(path=path, media_type=blob.media_type, headers={"Cache-Control": "private, no-store", "ETag": blob.sha256})


@router.delete("/references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference(reference_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    reference = _reference_access(db, user, reference_id)
    if reference.project_id and reference.user_id != user.id:
        require_project_role(db, user, reference.project_id, "manager")
    active = db.scalar(
        select(func.count(ImageEditRequest.id)).where(
            ImageEditRequest.status.in_(["queued", "planning", "generating"]),
            or_(
                ImageEditRequest.source_reference_id == reference.id,
                ImageEditRequest.mask_reference_id == reference.id,
                ImageEditRequest.identity_reference_id == reference.id,
            ),
        )
    ) or 0
    if int(active) > 0:
        raise HTTPException(status_code=409, detail="Reference is used by an active image edit")
    try:
        unlink_after_commit = delete_reference_bytes(db, reference, storage_root=request.app.state.settings.image_storage_path)
        db.commit()
    except ImageReferenceError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if unlink_after_commit is not None:
        try:
            unlink_after_commit.unlink(missing_ok=True)
        except OSError:
            # DB ownership is already detached. A later maintenance sweep can
            # remove the inaccessible orphan without risking a live reference.
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/generations/{generation_id}/reference", response_model=ImageReferenceRead, status_code=status.HTTP_201_CREATED)
def generation_as_reference(
    generation_id: str,
    kind: Literal["edit_source", "identity"] = Query(default="edit_source"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImageReference:
    generation = db.get(ImageGeneration, generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Image generation not found")
    if generation.project_id:
        require_project_role(db, user, generation.project_id, "member")
    elif generation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Image generation not found")
    if generation.status != "ready" or generation.qa_status != "passed" or not generation.blob_id:
        raise HTTPException(status_code=409, detail="Only a QA-passed image can become an edit reference")
    blob = db.get(ImageBlob, generation.preferred_blob_id or generation.blob_id)
    if blob is None or not Path(blob.storage_path).is_file():
        raise HTTPException(status_code=410, detail="Generated image blob is unavailable")
    reference = ImageReference(
        user_id=user.id,
        project_id=generation.project_id,
        blob_id=blob.id,
        kind=kind,
        original_name=f"generation-{generation.id}.png",
        status="ready",
        metadata_json={"source_generation_id": generation.id, "sha256": blob.sha256, "width": blob.width, "height": blob.height},
    )
    db.add(reference)
    db.commit()
    db.refresh(reference)
    return reference


@router.post("/edits", response_model=ImageEditCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_edit(payload: ImageEditCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ImageEditCreateResponse:
    require_capability(db, user.id, "images")
    settings = request.app.state.settings
    source = _reference_access(db, user, payload.source_reference_id, "viewer")
    if source.kind not in {"edit_source", "identity"}:
        raise HTTPException(status_code=422, detail="Mask reference cannot be used as an edit source")
    project_id = payload.project_id or source.project_id
    if payload.project_id and source.project_id and payload.project_id != source.project_id:
        raise HTTPException(status_code=409, detail="Edit project and source reference project differ")
    if project_id:
        require_project_role(db, user, project_id, "member")

    mask = None
    if payload.mask_reference_id:
        mask = _reference_access(db, user, payload.mask_reference_id, "viewer")
        if mask.kind != "mask":
            raise HTTPException(status_code=422, detail="mask_reference_id must point to a mask")
        source_link = str((mask.metadata_json or {}).get("source_reference_id") or "")
        if source_link and source_link != source.id:
            raise HTTPException(status_code=409, detail="Mask belongs to another source image")
    identity = None
    if payload.identity_reference_id:
        identity = _reference_access(db, user, payload.identity_reference_id, "viewer")
        if identity.kind not in {"identity", "edit_source"}:
            raise HTTPException(status_code=422, detail="identity_reference_id is not identity-capable")
        if identity.project_id and project_id and identity.project_id != project_id:
            raise HTTPException(status_code=409, detail="Project-scoped identity reference belongs to another project")

    policy = published_policy(db)
    decision = evaluate_prompt(policy, payload.instruction)
    if not decision.allowed:
        raise HTTPException(status_code=422, detail={"code": "image_policy_blocked", "rule": decision.rule})
    try:
        resolved_mode = resolve_edit_mode(payload.mode, payload.instruction)
    except ImageEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    worker = image_worker_snapshot(db, stale_seconds=90)
    caps = image_edit_capabilities(settings, worker_alive=bool(worker.get("alive")))
    if str(caps["backend"]) == "disabled" or "unsupported_image_edit_backend" in caps["reasons"]:
        raise HTTPException(status_code=503, detail="Local image editing backend is not configured")
    if not caps["worker_alive"]:
        raise HTTPException(status_code=503, detail="Image worker is not running")
    if settings.image_edit_require_vision_qa and not caps["vision_ready"]:
        raise HTTPException(status_code=503, detail="Image editing requires the trusted local Qwen Vision QA endpoint")
    if resolved_mode == "identity_recompose" and not caps["identity_recompose"]:
        raise HTTPException(status_code=503, detail="Identity-preserving scene editing is not configured")
    if resolved_mode in _LOCAL_EDIT_MODES or resolved_mode == "auto":
        if not caps["local_object_edit"]:
            raise HTTPException(status_code=503, detail="Local object editing model is not configured")
    if payload.strict_quality and resolved_mode in {"remove_object", "replace_object", "add_object"} and not payload.preserve_outside_mask:
        raise HTTPException(status_code=422, detail="Strict local object editing requires preserve_outside_mask=true")
    if resolved_mode == "auto" and not payload.mask_reference_id and not caps["vision_ready"]:
        raise HTTPException(status_code=503, detail="Automatic edit localization requires trusted local vision QA")

    try:
        ensure_disk_capacity(settings.image_storage_path, min_free_bytes=settings.image_storage_min_free_bytes, min_free_percent=settings.image_storage_min_free_percent)
    except ImageRuntimeError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    if total_user_image_storage_bytes(db, user.id) >= settings.image_user_storage_quota_bytes:
        raise HTTPException(status_code=507, detail="User image storage quota reached")
    active = db.scalar(select(func.count(ImageGeneration.id)).where(ImageGeneration.user_id == user.id, ImageGeneration.status.in_(["queued", "generating"]))) or 0
    if int(active) >= settings.image_max_active_per_user:
        raise HTTPException(status_code=429, detail="Too many active image jobs")

    source_blob = db.get(ImageBlob, source.blob_id)
    if source_blob is None:
        raise HTTPException(status_code=410, detail="Source image blob is unavailable")
    if mask:
        mask_blob = db.get(ImageBlob, mask.blob_id)
        if mask_blob is None or (mask_blob.width, mask_blob.height) != (source_blob.width, source_blob.height):
            raise HTTPException(status_code=422, detail="Mask dimensions must match source image")

    steps = payload.steps or settings.image_default_steps
    if steps > settings.image_max_steps:
        raise HTTPException(status_code=422, detail="Image step budget exceeded")
    seed = payload.seed if payload.seed is not None else secrets.randbelow(2**31)
    model_name = settings.image_edit_identity_model_name if resolved_mode == "identity_recompose" else settings.image_edit_model_name
    if resolved_mode == "identity_recompose" and not str(model_name or "").strip() and caps["backend"] == "qwen-image-edit":
        model_name = settings.image_edit_model_name

    generation = ImageGeneration(
        user_id=user.id,
        project_id=project_id,
        prompt=payload.instruction.strip(),
        negative_prompt="",
        status="queued",
        backend=settings.image_edit_backend,
        model_name=model_name or "local-image-edit",
        width=source_blob.width,
        height=source_blob.height,
        steps=steps,
        seed=seed,
        safety_policy_id=policy.id if policy else None,
        safety_status="allowed" if policy else "no_policy",
        delivery_status="active",
        manifest={
            "operation": "image_edit",
            "requested_mode": payload.mode,
            "resolved_mode_at_admission": resolved_mode,
            "source_reference_id": source.id,
            "mask_reference_id": mask.id if mask else None,
            "identity_reference_id": identity.id if identity else None,
            "strict_quality": payload.strict_quality,
            "preserve_identity": payload.preserve_identity,
            "preserve_outside_mask": payload.preserve_outside_mask,
            "worker_heartbeat_at_admission": worker.get("last_checked_at"),
        },
    )
    db.add(generation)
    db.flush()
    edit = ImageEditRequest(
        generation_id=generation.id,
        user_id=user.id,
        project_id=project_id,
        source_reference_id=source.id,
        mask_reference_id=mask.id if mask else None,
        identity_reference_id=identity.id if identity else None,
        mode=payload.mode,
        instruction=payload.instruction.strip(),
        preserve_identity=payload.preserve_identity,
        preserve_outside_mask=payload.preserve_outside_mask,
        strict_quality=payload.strict_quality,
        status="queued",
        plan={},
        qa_summary={},
        error_message="",
    )
    db.add(edit)
    db.flush()
    job = enqueue_job(
        db,
        kind="image.edit",
        payload={"generation_id": generation.id, "edit_request_id": edit.id},
        user_id=user.id,
        project_id=project_id,
        priority=settings.image_job_priority,
        max_attempts=2,
        idempotency_key=f"image-edit:{edit.id}",
    )
    generation.job_id = job.id
    db.commit()
    db.refresh(edit)
    return ImageEditCreateResponse(edit=edit, generation_id=generation.id, job_id=job.id, status=generation.status)


@router.get("/edits", response_model=list[ImageEditRead])
def list_edits(project_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ImageEditRequest]:
    if project_id:
        require_project_role(db, user, project_id, "viewer")
        stmt = select(ImageEditRequest).where(ImageEditRequest.project_id == project_id)
    else:
        stmt = select(ImageEditRequest).where(ImageEditRequest.user_id == user.id)
    return list(db.scalars(stmt.order_by(ImageEditRequest.created_at.desc()).limit(100)).all())


@router.get("/edits/{edit_id}", response_model=ImageEditRead)
def get_edit(edit_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ImageEditRequest:
    return _edit_access(db, user, edit_id)


@router.post("/edits/{edit_id}/cancel", response_model=ImageEditRead)
def cancel_edit(edit_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ImageEditRequest:
    edit = _edit_access(db, user, edit_id)
    if edit.user_id != user.id and edit.project_id:
        require_project_role(db, user, edit.project_id, "manager")
    generation = db.get(ImageGeneration, edit.generation_id)
    if edit.status != "queued" or generation is None or generation.status != "queued":
        raise HTTPException(status_code=409, detail="Only a queued edit can be cancelled safely")
    edit.status = "cancelled"
    edit.updated_at = utcnow()
    generation.status = "cancelled"
    if generation.job_id:
        job = db.get(BackgroundJob, generation.job_id)
        if job and job.status == "queued":
            job.status = "cancelled"
    db.commit()
    db.refresh(edit)
    return edit
