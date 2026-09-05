from __future__ import annotations

import hashlib
import io
import random
import shutil
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageChops, ImageDraw, ImageStat, features
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ImageBlob, ImageGeneration, ImageQAEvent, ImageSafetyPolicy, ImageVariant, utcnow
from app.services.image_policy import compose_effective_prompt, compose_negative_prompt


class ImageRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class ImageQAResult:
    passed: bool
    findings: list[dict]
    metrics: dict


class SemanticImageQA(Protocol):
    def check(self, *, content: bytes, media_type: str, user_prompt: str, policy_superprompt: str = "") -> ImageQAResult: ...


class ImageBackend(Protocol):
    name: str
    model_name: str
    def generate(self, *, prompt: str, negative_prompt: str, width: int, height: int, steps: int, seed: int) -> GeneratedImage: ...


class DisabledImageBackend:
    name = "disabled"
    model_name = ""
    def generate(self, **kwargs) -> GeneratedImage:
        raise ImageRuntimeError("Local image backend is not configured")


class LocalDiffusersBackend:
    """Optional fully-local backend. Model files must already exist on disk."""
    name = "diffusers"

    def __init__(self, model_path: str, model_name: str = ""):
        path = Path(model_path).expanduser().resolve()
        if not path.is_dir():
            raise ImageRuntimeError("Local image model directory does not exist")
        self.model_path = path
        self.model_name = model_name or path.name
        self._pipeline = None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            import torch
            from diffusers import DiffusionPipeline
        except ImportError as exc:
            raise ImageRuntimeError("Install the optional 'image' dependencies to use diffusers") from exc
        pipeline = DiffusionPipeline.from_pretrained(str(self.model_path), local_files_only=True, torch_dtype=torch.float32)
        pipeline = pipeline.to("cpu")
        pipeline.set_progress_bar_config(disable=True)
        self._pipeline = pipeline
        return pipeline

    def generate(self, *, prompt: str, negative_prompt: str, width: int, height: int, steps: int, seed: int) -> GeneratedImage:
        pipeline = self._load()
        import torch
        generator = torch.Generator(device="cpu").manual_seed(seed)
        output = pipeline(prompt=prompt, negative_prompt=negative_prompt or None, width=width, height=height, num_inference_steps=steps, generator=generator)
        if not getattr(output, "images", None):
            raise ImageRuntimeError("Local image backend returned no image")
        out = io.BytesIO(); output.images[0].save(out, format="PNG", optimize=True)
        return GeneratedImage(out.getvalue())


class MockImageBackend:
    """Deterministic lightweight backend for tests and runtime smoke checks only."""
    name = "mock"
    model_name = "x1-mock-image"
    def generate(self, *, prompt: str, negative_prompt: str, width: int, height: int, steps: int, seed: int) -> GeneratedImage:
        rng = random.Random(seed)
        image = Image.new("RGB", (width, height), (rng.randrange(32, 224), rng.randrange(32, 224), rng.randrange(32, 224)))
        draw = ImageDraw.Draw(image)
        draw.text((16, 16), hashlib.sha256(prompt.encode()).hexdigest()[:16], fill=(255, 255, 255))
        out = io.BytesIO(); image.save(out, format="PNG", optimize=True)
        return GeneratedImage(out.getvalue())


def validate_dimensions(width: int, height: int, *, max_dimension: int, max_pixels: int) -> None:
    if width > max_dimension or height > max_dimension:
        raise ImageRuntimeError(f"Image dimension exceeds {max_dimension}px limit")
    if width * height > max_pixels:
        raise ImageRuntimeError("Image pixel budget exceeded")
    if width % 8 or height % 8:
        raise ImageRuntimeError("Image dimensions must be divisible by 8")


def ensure_disk_capacity(storage_root: str, *, min_free_bytes: int, min_free_percent: float) -> dict:
    root = Path(storage_root).expanduser().resolve()
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
    if usage.free < min_free_bytes or free_percent < min_free_percent:
        raise ImageRuntimeError("Image storage low-water mark reached; new generation is temporarily blocked")
    return {"total_bytes": usage.total, "free_bytes": usage.free, "free_percent": round(free_percent, 3)}


def _image_info(content: bytes) -> tuple[int, int, str]:
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
            fmt = (img.format or "PNG").upper()
    except Exception as exc:
        raise ImageRuntimeError("Backend returned an invalid image") from exc
    media = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "AVIF": "image/avif"}.get(fmt, "application/octet-stream")
    return width, height, media


def persist_blob(db: Session, *, storage_root: str, content: bytes) -> ImageBlob:
    sha = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == sha))
    if existing is not None:
        return existing
    width, height, media_type = _image_info(content)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/avif": ".avif"}.get(media_type, ".bin")
    root = Path(storage_root).resolve(); target = root / sha[:2] / f"{sha}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".tmp"); tmp.write_bytes(content); tmp.replace(target)
    blob = ImageBlob(sha256=sha, media_type=media_type, width=width, height=height, size_bytes=len(content), storage_path=str(target))
    try:
        with db.begin_nested():
            db.add(blob)
            db.flush()
        return blob
    except IntegrityError:
        existing = db.scalar(select(ImageBlob).where(ImageBlob.sha256 == sha))
        if existing is None:
            raise
        return existing


def deterministic_image_qa(content: bytes, *, expected_width: int, expected_height: int) -> ImageQAResult:
    findings: list[dict] = []
    try:
        with Image.open(io.BytesIO(content)) as raw:
            img = raw.convert("RGBA")
    except Exception:
        return ImageQAResult(False, [{"code": "invalid_image", "severity": "critical", "repairable": True}], {})
    width, height = img.size
    if (width, height) != (expected_width, expected_height):
        findings.append({"code": "wrong_dimensions", "severity": "critical", "repairable": True})
    rgb = Image.new("RGB", img.size, "white"); rgb.paste(img.convert("RGB"), mask=img.getchannel("A"))
    stat = ImageStat.Stat(rgb)
    variance = sum(stat.var) / max(1, len(stat.var))
    extrema = rgb.getextrema()
    dynamic_range = sum(hi - lo for lo, hi in extrema) / 3.0
    alpha = img.getchannel("A")
    alpha_stat = ImageStat.Stat(alpha)
    alpha_mean = float(alpha_stat.mean[0])
    if variance < 2.0 or dynamic_range < 4.0:
        findings.append({"code": "near_blank", "severity": "critical", "repairable": True})
    if alpha_mean < 3.0:
        findings.append({"code": "transparent_blank", "severity": "critical", "repairable": True})
    edge = max(1, min(width, height) // 100)
    center = rgb.crop((edge, edge, max(edge + 1, width - edge), max(edge + 1, height - edge)))
    border_mask = Image.new("L", (width, height), 255); ImageDraw.Draw(border_mask).rectangle((edge, edge, width-edge-1, height-edge-1), fill=0)
    border_pixels = ImageStat.Stat(rgb, border_mask).var
    center_var = sum(ImageStat.Stat(center).var) / 3.0 if center.size[0] and center.size[1] else 0.0
    border_var = sum(border_pixels) / 3.0
    if center_var > 30 and border_var > center_var * 2.8:
        findings.append({"code": "edge_activity_high", "severity": "warning", "repairable": False})
    critical = any(f["severity"] == "critical" for f in findings)
    metrics = {"width": width, "height": height, "variance": round(variance, 3), "dynamic_range": round(dynamic_range, 3), "alpha_mean": round(alpha_mean, 3), "border_variance": round(border_var, 3), "center_variance": round(center_var, 3)}
    return ImageQAResult(not critical, findings, metrics)


def _perceptual_error(original: Image.Image, candidate: Image.Image) -> float:
    a = original.convert("RGB")
    b = candidate.convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def _encode_variant(image: Image.Image, codec: str, quality: int, *, max_side: int | None = None) -> tuple[bytes, float]:
    original = image.convert("RGB")
    target = original.copy()
    if max_side and max(target.size) > max_side:
        target.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    fmt = codec.upper()
    kwargs = {"quality": quality}
    if fmt == "WEBP": kwargs.update({"method": 6})
    target.save(out, format=fmt, **kwargs)
    content = out.getvalue()
    with Image.open(io.BytesIO(content)) as decoded:
        reference = original if not max_side else target
        error = _perceptual_error(reference, decoded)
    return content, error


def create_storage_variants(db: Session, *, source_blob: ImageBlob, storage_root: str, max_error: float, preview_max_side: int) -> list[ImageVariant]:
    with Image.open(source_blob.storage_path) as img:
        original = img.convert("RGB")
    created: list[ImageVariant] = []
    codecs = ["WEBP"] + (["AVIF"] if features.check("avif") else [])
    for codec in codecs:
        best: tuple[bytes, float, int] | None = None
        for quality in (90, 85, 80, 75, 70):
            try:
                content, error = _encode_variant(original, codec, quality)
            except Exception:
                break
            if error <= max_error:
                best = (content, error, quality)
            else:
                break
        if best:
            content, error, quality = best
            blob = persist_blob(db, storage_root=storage_root, content=content)
            row = db.scalar(select(ImageVariant).where(ImageVariant.source_blob_id == source_blob.id, ImageVariant.kind == f"full_{codec.lower()}"))
            if row is None:
                row = ImageVariant(source_blob_id=source_blob.id, blob_id=blob.id, kind=f"full_{codec.lower()}", codec=codec.lower(), quality=quality, perceptual_error=round(error, 4)); db.add(row); db.flush()
            created.append(row)
    preview_content, preview_error = _encode_variant(original, "WEBP", 80, max_side=preview_max_side)
    preview_blob = persist_blob(db, storage_root=storage_root, content=preview_content)
    preview = db.scalar(select(ImageVariant).where(ImageVariant.source_blob_id == source_blob.id, ImageVariant.kind == "preview_webp"))
    if preview is None:
        preview = ImageVariant(source_blob_id=source_blob.id, blob_id=preview_blob.id, kind="preview_webp", codec="webp", quality=80, perceptual_error=round(preview_error, 4)); db.add(preview); db.flush()
    created.append(preview)
    return created


def choose_preferred_blob(db: Session, source_blob: ImageBlob, variants: list[ImageVariant]) -> ImageBlob:
    candidates = [source_blob]
    for variant in variants:
        if variant.kind.startswith("full_"):
            blob = db.get(ImageBlob, variant.blob_id)
            if blob is not None:
                candidates.append(blob)
    return min(candidates, key=lambda b: b.size_bytes)


def run_generation_qa(db: Session, generation: ImageGeneration, *, blob: ImageBlob, attempt: int) -> ImageQAResult:
    content = Path(blob.storage_path).read_bytes()
    result = deterministic_image_qa(content, expected_width=generation.width, expected_height=generation.height)
    db.add(ImageQAEvent(generation_id=generation.id, attempt=attempt, status="passed" if result.passed else "failed", findings=result.findings, metrics=result.metrics))
    generation.qa_status = "passed" if result.passed else "failed"
    db.flush()
    return result


def execute_generation(db: Session, generation: ImageGeneration, *, backend: ImageBackend, storage_root: str, max_perceptual_error: float = 4.0, preview_max_side: int = 512, max_repairs: int = 1, semantic_qa: SemanticImageQA | None = None) -> ImageGeneration:
    if generation.status not in {"queued", "generating"}:
        raise ImageRuntimeError("Generation is not runnable")
    generation.status = "generating"; generation.started_at = generation.started_at or utcnow(); db.flush()
    policy = db.get(ImageSafetyPolicy, generation.safety_policy_id) if generation.safety_policy_id else None
    effective_prompt = compose_effective_prompt(policy, generation.prompt)
    policy_negative = compose_negative_prompt(policy, generation.negative_prompt)
    try:
        attempt = 0
        final_blob: ImageBlob | None = None
        qa: ImageQAResult | None = None
        used_seed = generation.seed
        while attempt <= max_repairs:
            attempt += 1
            repair_codes = [f["code"] for f in (qa.findings if qa else []) if f.get("repairable")]
            retry_negative = policy_negative
            if repair_codes:
                retry_negative = ", ".join(filter(None, [retry_negative, "avoid " + ", ".join(repair_codes)]))
                used_seed = (generation.seed + attempt - 1) % (2**31)
            result = backend.generate(prompt=effective_prompt, negative_prompt=retry_negative, width=generation.width, height=generation.height, steps=generation.steps, seed=used_seed)
            blob = persist_blob(db, storage_root=storage_root, content=result.content)
            if blob.width != generation.width or blob.height != generation.height:
                raise ImageRuntimeError("Backend returned unexpected image dimensions")
            qa = run_generation_qa(db, generation, blob=blob, attempt=attempt)
            final_blob = blob
            if qa.passed:
                break
            if attempt > max_repairs or not any(f.get("repairable") for f in qa.findings):
                break
            generation.repair_attempts += 1
        if final_blob is None or qa is None or not qa.passed:
            generation.blob_id = final_blob.id if final_blob else None
            generation.status = "qa_failed"; generation.finished_at = utcnow()
            generation.error_message = "Image failed deterministic QA"
            generation.manifest = {**(generation.manifest or {}), "qa_status": "failed", "qa_findings": qa.findings if qa else []}
            db.flush(); return generation
        if semantic_qa is not None:
            content = Path(final_blob.storage_path).read_bytes()
            semantic = semantic_qa.check(content=content, media_type=final_blob.media_type, user_prompt=generation.prompt, policy_superprompt=policy.superprompt if policy else "")
            db.add(ImageQAEvent(generation_id=generation.id, attempt=attempt, qa_type="semantic", status="passed" if semantic.passed else "failed", findings=semantic.findings, metrics=semantic.metrics))
            if not semantic.passed:
                generation.blob_id = final_blob.id
                generation.qa_status = "failed"
                generation.status = "qa_failed"
                generation.finished_at = utcnow()
                generation.error_message = "Image failed semantic vision QA"
                generation.manifest = {**(generation.manifest or {}), "qa_status": "failed", "semantic_findings": semantic.findings}
                db.flush(); return generation
        variants = create_storage_variants(db, source_blob=final_blob, storage_root=storage_root, max_error=max_perceptual_error, preview_max_side=preview_max_side)
        preferred = choose_preferred_blob(db, final_blob, variants)
        generation.blob_id = final_blob.id; generation.preferred_blob_id = preferred.id; generation.status = "ready"; generation.finished_at = utcnow(); generation.backend = backend.name; generation.model_name = backend.model_name
        generation.manifest = {"backend": backend.name, "model": backend.model_name, "safety_policy_id": policy.id if policy else None, "safety_policy_version": policy.version if policy else None, "seed": generation.seed, "effective_seed": used_seed, "steps": generation.steps, "width": generation.width, "height": generation.height, "blob_sha256": final_blob.sha256, "preferred_sha256": preferred.sha256, "size_bytes": final_blob.size_bytes, "preferred_size_bytes": preferred.size_bytes, "qa_status": "passed", "repair_attempts": generation.repair_attempts, "variants": [{"kind": v.kind, "blob_id": v.blob_id, "codec": v.codec, "quality": v.quality, "perceptual_error": v.perceptual_error} for v in variants]}
        generation.error_message = ""
    except Exception as exc:
        generation.status = "failed"; generation.finished_at = utcnow(); generation.error_message = str(exc)[:2000]
        raise
    finally:
        db.flush()
    return generation


def user_image_storage_bytes(db: Session, user_id: str) -> int:
    source_ids = {row[0] for row in db.execute(select(ImageGeneration.blob_id).where(ImageGeneration.user_id == user_id, ImageGeneration.blob_id.is_not(None))).all() if row[0]}
    if not source_ids:
        return 0
    ids = set(source_ids)
    ids.update(v for v in db.scalars(select(ImageVariant.blob_id).where(ImageVariant.source_blob_id.in_(source_ids))).all() if v)
    return int(db.scalar(select(func.coalesce(func.sum(ImageBlob.size_bytes), 0)).where(ImageBlob.id.in_(ids))) or 0)


def cleanup_expired_rejected_generations(db: Session, *, retention_days: int) -> int:
    cutoff = utcnow() - timedelta(days=max(0, retention_days))
    rows = list(db.scalars(select(ImageGeneration).where(
        ImageGeneration.status.in_(["qa_failed", "failed", "cancelled"]),
        ImageGeneration.finished_at.is_not(None),
        ImageGeneration.finished_at < cutoff,
        or_(ImageGeneration.blob_id.is_not(None), ImageGeneration.preferred_blob_id.is_not(None)),
    )).all())
    for generation in rows:
        generation.blob_id = None
        generation.preferred_blob_id = None
        generation.manifest = {**(generation.manifest or {}), "storage_pruned_at": utcnow().isoformat()}
    db.flush()
    return len(rows)


def prune_unreferenced_image_blobs(db: Session, *, storage_root: str, limit: int = 200) -> int:
    """Prune variants and physical blobs only when no generation still references the source asset."""
    referenced_sources = select(ImageGeneration.blob_id).where(ImageGeneration.blob_id.is_not(None))
    orphan_variants = list(db.scalars(select(ImageVariant).where(~ImageVariant.source_blob_id.in_(referenced_sources))).all())
    for variant in orphan_variants:
        db.delete(variant)
    db.flush()

    rows = list(db.scalars(
        select(ImageBlob).where(
            ~ImageBlob.id.in_(select(ImageGeneration.blob_id).where(ImageGeneration.blob_id.is_not(None))),
            ~ImageBlob.id.in_(select(ImageGeneration.preferred_blob_id).where(ImageGeneration.preferred_blob_id.is_not(None))),
            ~ImageBlob.id.in_(select(ImageVariant.blob_id)),
            ~ImageBlob.id.in_(select(ImageVariant.source_blob_id)),
        ).limit(limit)
    ).all())
    removed = 0
    root = Path(storage_root).resolve()
    for blob in rows:
        path = Path(blob.storage_path).resolve()
        try:
            if path.is_relative_to(root) and path.is_file():
                path.unlink()
        except (OSError, ValueError):
            continue
        db.delete(blob); removed += 1
    db.flush()
    return removed
