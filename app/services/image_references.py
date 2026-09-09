from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ImageBlob, ImageGeneration, ImageReference, ImageTrainingExample, ImageVariant, User, utcnow
from app.services.image_runtime import ensure_disk_capacity, user_image_storage_bytes


class ImageReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    width: int
    height: int
    media_type: str
    sha256: str


def safe_image_name(value: str) -> str:
    name = Path((value or "image.png").replace("\\", "/")).name.strip() or "image.png"
    return name[:240]


def _load_verified(data: bytes, *, max_dimension: int, max_pixels: int, mask: bool) -> Image.Image:
    if not data:
        raise ImageReferenceError("Image is empty")
    try:
        probe = Image.open(io.BytesIO(data))
        if int(getattr(probe, "n_frames", 1) or 1) != 1:
            raise ImageReferenceError("Animated images are not supported as edit references")
        width, height = probe.size
        if width < 32 or height < 32:
            raise ImageReferenceError("Reference image is too small")
        if width > max_dimension or height > max_dimension or width * height > max_pixels:
            raise ImageReferenceError("Reference image dimensions exceed the safe limit")
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image)
    except ImageReferenceError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageReferenceError("Unsupported or damaged image") from exc

    if mask:
        image = image.getchannel("A") if "A" in image.getbands() else image.convert("L")
        image = image.point(lambda value: 255 if value >= 128 else 0, mode="L")
    else:
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
    return image


def normalize_reference_image(data: bytes, *, kind: str, max_dimension: int, max_pixels: int) -> NormalizedImage:
    if kind not in {"edit_source", "identity", "mask"}:
        raise ImageReferenceError("Unsupported image reference kind")
    image = _load_verified(data, max_dimension=max_dimension, max_pixels=max_pixels, mask=kind == "mask")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    content = output.getvalue()
    return NormalizedImage(content=content, width=image.width, height=image.height, media_type="image/png", sha256=hashlib.sha256(content).hexdigest())


def user_reference_storage_bytes(db: Session, user_id: str) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(ImageBlob.size_bytes), 0))
        .select_from(ImageReference)
        .join(ImageBlob, ImageBlob.id == ImageReference.blob_id)
        .where(ImageReference.user_id == user_id, ImageReference.status == "ready")
    ) or 0
    return int(total)


def total_user_image_storage_bytes(db: Session, user_id: str) -> int:
    return int(user_image_storage_bytes(db, user_id)) + user_reference_storage_bytes(db, user_id)


def _canonical_reference_path(root: Path, digest: str) -> Path:
    return root / "references" / digest[:2] / f"{digest}.png"


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_reference(
    db: Session,
    *,
    user: User,
    project_id: str | None,
    filename: str,
    kind: str,
    data: bytes,
    settings,
    source_reference_id: str | None = None,
) -> ImageReference:
    if len(data) > int(settings.image_edit_max_source_bytes):
        raise ImageReferenceError("Reference image exceeds the upload byte limit")
    normalized = normalize_reference_image(data, kind=kind, max_dimension=int(settings.image_edit_max_source_dimension), max_pixels=int(settings.image_edit_max_source_pixels))
    if kind == "mask":
        if not source_reference_id:
            raise ImageReferenceError("Mask requires a source reference")
        source = db.get(ImageReference, source_reference_id)
        if source is None or source.status != "ready" or source.kind == "mask" or not source.blob_id:
            raise ImageReferenceError("Mask source reference is invalid")
        source_blob = db.get(ImageBlob, source.blob_id)
        if source_blob is None:
            raise ImageReferenceError("Mask source blob is unavailable")
        if (normalized.width, normalized.height) != (source_blob.width, source_blob.height):
            raise ImageReferenceError("Mask dimensions must exactly match the source image")
    if total_user_image_storage_bytes(db, user.id) + len(normalized.content) > int(settings.image_user_storage_quota_bytes):
        raise ImageReferenceError("User image storage quota reached")
    ensure_disk_capacity(settings.image_storage_path, min_free_bytes=settings.image_storage_min_free_bytes, min_free_percent=settings.image_storage_min_free_percent)

    root = Path(settings.image_storage_path).resolve()
    path = _canonical_reference_path(root, normalized.sha256)
    existing_blob = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == normalized.sha256))
    if existing_blob is None:
        _write_atomic(path, normalized.content)
        blob = ImageBlob(sha256=normalized.sha256, media_type=normalized.media_type, width=normalized.width, height=normalized.height, size_bytes=len(normalized.content), storage_path=str(path))
        try:
            with db.begin_nested():
                db.add(blob)
                db.flush()
            existing_blob = blob
        except IntegrityError:
            existing_blob = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == normalized.sha256))
            if existing_blob is None:
                raise
            winner_path = Path(existing_blob.storage_path).resolve()
            if path.exists() and path.resolve() != winner_path:
                path.unlink(missing_ok=True)
    else:
        blob_path = Path(existing_blob.storage_path)
        if not blob_path.is_file():
            try:
                blob_path.resolve().relative_to(root)
            except ValueError as exc:
                raise ImageReferenceError("Existing image blob path is outside image storage") from exc
            _write_atomic(blob_path, normalized.content)

    reference = ImageReference(
        user_id=user.id,
        project_id=project_id,
        blob_id=existing_blob.id,
        kind=kind,
        original_name=safe_image_name(filename),
        status="ready",
        metadata_json={
            "normalized": True,
            "source_reference_id": source_reference_id,
            "sha256": normalized.sha256,
            "width": normalized.width,
            "height": normalized.height,
            "media_type": normalized.media_type,
            "original_upload_bytes": len(data),
        },
    )
    db.add(reference)
    db.flush()
    return reference


def load_reference_image(db: Session, reference: ImageReference) -> tuple[ImageBlob, Image.Image]:
    if reference.status != "ready" or not reference.blob_id:
        raise ImageReferenceError("Image reference is not available")
    blob = db.get(ImageBlob, reference.blob_id)
    if blob is None:
        raise ImageReferenceError("Image reference blob is missing")
    path = Path(blob.storage_path)
    if not path.is_file():
        raise ImageReferenceError("Image reference content is unavailable")
    try:
        image = Image.open(path)
        image.load()
        image = image.convert("L" if reference.kind == "mask" else "RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageReferenceError("Stored image reference is damaged") from exc
    return blob, image


def delete_reference_bytes(db: Session, reference: ImageReference, *, storage_root: str) -> Path | None:
    """Detach private input bytes and delete the blob when it has no other owner."""
    if reference.status == "deleted":
        return None
    blob_id = reference.blob_id
    reference.status = "deleted"
    reference.deleted_at = utcnow()
    if not blob_id:
        db.flush()
        return None

    blob = db.scalar(select(ImageBlob).where(ImageBlob.id == blob_id).with_for_update())
    reference.blob_id = None
    reference.metadata_json = {**(reference.metadata_json or {}), "bytes_deleted": True, "detached_blob_id": blob_id}
    db.flush()
    if blob is None:
        return None

    other_reference = db.scalar(select(ImageReference.id).where(ImageReference.blob_id == blob_id).limit(1))
    generation_use = db.scalar(select(ImageGeneration.id).where(or_(ImageGeneration.blob_id == blob_id, ImageGeneration.preferred_blob_id == blob_id)).limit(1))
    variant_use = db.scalar(select(ImageVariant.id).where(or_(ImageVariant.source_blob_id == blob_id, ImageVariant.blob_id == blob_id)).limit(1))
    training_use = db.scalar(select(ImageTrainingExample.id).where(ImageTrainingExample.blob_id == blob_id).limit(1))
    if any((other_reference, generation_use, variant_use, training_use)):
        return None

    root = Path(storage_root).resolve()
    path = Path(blob.storage_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ImageReferenceError("Image blob path is outside configured image storage") from exc
    db.delete(blob)
    db.flush()
    return path
