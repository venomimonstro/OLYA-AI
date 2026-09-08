from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ImageBlob, ImageReference, User
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
        if "A" in image.getbands():
            image = image.getchannel("A")
        else:
            image = image.convert("L")
        # A mask is a deliberate user selection, not a photograph. Normalize to
        # binary pixels so a crafted grayscale upload cannot silently alter large
        # low-opacity areas outside the intended edit region.
        image = image.point(lambda value: 255 if value >= 128 else 0, mode="L")
    else:
        # Strip EXIF/ICC/GPS metadata by decoding and re-encoding a pixel-only RGB
        # image. This also normalizes parser edge cases before model consumption.
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
    return image


def normalize_reference_image(
    data: bytes,
    *,
    kind: str,
    max_dimension: int,
    max_pixels: int,
) -> NormalizedImage:
    if kind not in {"edit_source", "identity", "mask"}:
        raise ImageReferenceError("Unsupported image reference kind")
    image = _load_verified(data, max_dimension=max_dimension, max_pixels=max_pixels, mask=kind == "mask")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    content = output.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    return NormalizedImage(content=content, width=image.width, height=image.height, media_type="image/png", sha256=digest)


def user_reference_storage_bytes(db: Session, user_id: str) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(ImageBlob.size_bytes), 0))
        .select_from(ImageReference)
        .join(ImageBlob, ImageBlob.id == ImageReference.blob_id)
        .where(ImageReference.user_id == user_id, ImageReference.status == "ready")
    ) or 0
    return int(total)


def total_user_image_storage_bytes(db: Session, user_id: str) -> int:
    # Conservative accounting deliberately counts a blob once as generation data
    # and again when it is retained as a reference. This prevents a dedupe trick
    # from turning references into an unbounded quota bypass.
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
    normalized = normalize_reference_image(
        data,
        kind=kind,
        max_dimension=int(settings.image_edit_max_source_dimension),
        max_pixels=int(settings.image_edit_max_source_pixels),
    )
    if total_user_image_storage_bytes(db, user.id) + len(normalized.content) > int(settings.image_user_storage_quota_bytes):
        raise ImageReferenceError("User image storage quota reached")
    ensure_disk_capacity(
        settings.image_storage_path,
        min_free_bytes=settings.image_storage_min_free_bytes,
        min_free_percent=settings.image_storage_min_free_percent,
    )

    root = Path(settings.image_storage_path).resolve()
    path = _canonical_reference_path(root, normalized.sha256)
    existing_blob = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == normalized.sha256))
    if existing_blob is None:
        _write_atomic(path, normalized.content)
        blob = ImageBlob(
            sha256=normalized.sha256,
            media_type=normalized.media_type,
            width=normalized.width,
            height=normalized.height,
            size_bytes=len(normalized.content),
            storage_path=str(path),
        )
        try:
            with db.begin_nested():
                db.add(blob)
                db.flush()
            existing_blob = blob
        except IntegrityError:
            existing_blob = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == normalized.sha256))
            if existing_blob is None:
                raise
    else:
        blob_path = Path(existing_blob.storage_path)
        if not blob_path.is_file():
            # Repair only when the recorded path is under the configured image
            # store; never let DB corruption redirect a write elsewhere.
            try:
                blob_path.resolve().relative_to(root)
            except ValueError as exc:
                raise ImageReferenceError("Existing image blob path is outside image storage") from exc
            _write_atomic(blob_path, normalized.content)

    metadata = {
        "normalized": True,
        "source_reference_id": source_reference_id,
        "sha256": normalized.sha256,
        "width": normalized.width,
        "height": normalized.height,
        "media_type": normalized.media_type,
        "original_upload_bytes": len(data),
    }
    reference = ImageReference(
        user_id=user.id,
        project_id=project_id,
        blob_id=existing_blob.id,
        kind=kind,
        original_name=safe_image_name(filename),
        status="ready",
        metadata_json=metadata,
    )
    db.add(reference)
    db.flush()
    return reference


def load_reference_image(db: Session, reference: ImageReference) -> tuple[ImageBlob, Image.Image]:
    if reference.status != "ready":
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
