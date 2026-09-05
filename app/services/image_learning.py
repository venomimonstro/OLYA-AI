from __future__ import annotations

import hashlib
import json
from pathlib import Path
from PIL import Image

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImageBlob, ImageDatasetSnapshot, ImageFeedback, ImageGeneration, ImageImprovementRun, ImageQAEvent, ImageTrainingExample, User, utcnow


def _perceptual_hash(db: Session, generation: ImageGeneration) -> str:
    if not generation.blob_id:
        return ""
    blob = db.get(ImageBlob, generation.blob_id)
    if blob is None or not Path(blob.storage_path).is_file():
        return ""
    with Image.open(blob.storage_path) as image:
        small = image.convert("L").resize((9, 8))
        pixels = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    bits = []
    for y in range(8):
        row = pixels[y * 9:(y + 1) * 9]
        bits.extend(row[x] > row[x + 1] for x in range(8))
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    return (int(a, 16) ^ int(b, 16)).bit_count()


class ImageLearningError(ValueError):
    pass


def _dedupe_key(g: ImageGeneration) -> str:
    blob = g.blob_id or ""
    raw = f"{blob}|{g.prompt.strip()}|{g.model_name}|{g.safety_policy_id or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def feedback_label(feedback: ImageFeedback, generation: ImageGeneration) -> str | None:
    if not feedback.allow_training:
        return None
    if feedback.rating >= 4 and generation.status == "ready" and generation.qa_status == "passed" and generation.delivery_status == "active":
        return "positive"
    if feedback.rating <= 2:
        return "regression"
    return None


def build_candidate(db: Session, generation: ImageGeneration, feedback: ImageFeedback) -> ImageTrainingExample | None:
    label = feedback_label(feedback, generation)
    if label is None:
        return None
    existing = db.scalar(select(ImageTrainingExample).where(ImageTrainingExample.generation_id == generation.id))
    if existing is not None:
        return existing
    phash = _perceptual_hash(db, generation)
    if phash:
        prior = db.scalars(select(ImageTrainingExample).where(ImageTrainingExample.label == label, ImageTrainingExample.state != "invalidated")).all()
        if any(_hamming(phash, row.perceptual_hash) <= 4 for row in prior if row.perceptual_hash):
            return None
    qa = list(db.scalars(select(ImageQAEvent).where(ImageQAEvent.generation_id == generation.id).order_by(ImageQAEvent.created_at)).all())
    provenance = {
        "generation_id": generation.id,
        "blob_sha256": (generation.manifest or {}).get("blob_sha256"),
        "prompt": generation.prompt,
        "negative_prompt": generation.negative_prompt,
        "model_name": generation.model_name,
        "backend": generation.backend,
        "seed": generation.seed,
        "steps": generation.steps,
        "width": generation.width,
        "height": generation.height,
        "policy_id": generation.safety_policy_id,
        "policy_version": (generation.manifest or {}).get("safety_policy_version"),
        "qa": [{"type": x.qa_type, "status": x.status, "findings": x.findings} for x in qa],
        "feedback_rating": feedback.rating,
        "feedback_user_id": feedback.user_id,
        "created_at": generation.created_at.isoformat() if generation.created_at else None,
    }
    row = ImageTrainingExample(
        generation_id=generation.id,
        blob_id=generation.blob_id,
        label=label,
        state="candidate",
        dedupe_key=_dedupe_key(generation),
        perceptual_hash=phash,
        provenance=provenance,
        created_by=feedback.user_id,
    )
    db.add(row)
    db.flush()
    return row


def refresh_candidates(db: Session) -> int:
    count = 0
    rows = db.execute(select(ImageFeedback, ImageGeneration).join(ImageGeneration, ImageGeneration.id == ImageFeedback.generation_id).where(ImageFeedback.allow_training.is_(True))).all()
    for feedback, generation in rows:
        before = db.scalar(select(ImageTrainingExample.id).where(ImageTrainingExample.generation_id == generation.id))
        row = build_candidate(db, generation, feedback)
        if row is not None and before is None:
            count += 1
    invalidate_ineligible(db)
    return count


def invalidate_ineligible(db: Session) -> int:
    changed = 0
    rows = list(db.scalars(select(ImageTrainingExample).where(ImageTrainingExample.state.in_(["candidate", "approved"]))).all())
    for row in rows:
        g = db.get(ImageGeneration, row.generation_id)
        if g is None or g.delivery_status != "active" or not g.blob_id:
            row.state = "invalidated"
            row.review_note = "Source generation is no longer eligible"
            row.reviewed_at = utcnow()
            for snap in db.scalars(select(ImageDatasetSnapshot).where(ImageDatasetSnapshot.state == "frozen")).all():
                if row.id in (snap.example_ids or []):
                    snap.state = "invalidated"
            changed += 1
    db.flush()
    return changed


def freeze_dataset(db: Session, actor: User) -> ImageDatasetSnapshot:
    invalidate_ineligible(db)
    rows = list(db.scalars(select(ImageTrainingExample).where(ImageTrainingExample.state == "approved").order_by(ImageTrainingExample.id)).all())
    positive = [r for r in rows if r.label == "positive"]
    regression = [r for r in rows if r.label == "regression"]
    if not positive:
        raise ImageLearningError("Dataset requires at least one approved positive example")
    manifest = {
        "schema": 1,
        "examples": [{"id": r.id, "generation_id": r.generation_id, "label": r.label, "dedupe_key": r.dedupe_key, "provenance": r.provenance} for r in rows],
    }
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    existing = db.scalar(select(ImageDatasetSnapshot).where(ImageDatasetSnapshot.manifest_sha256 == sha))
    if existing is not None:
        return existing
    snap = ImageDatasetSnapshot(
        manifest_sha256=sha,
        example_ids=[r.id for r in rows],
        positive_count=len(positive),
        regression_count=len(regression),
        manifest=manifest,
        created_by=actor.id,
    )
    db.add(snap)
    db.flush()
    return snap


def withdraw_training_consent(db: Session, generation_id: str, user_id: str) -> None:
    row = db.scalar(select(ImageTrainingExample).where(ImageTrainingExample.generation_id == generation_id))
    if row is None:
        return
    row.state = "invalidated"
    row.review_note = "Training consent withdrawn"
    row.reviewed_at = utcnow()
    for snap in db.scalars(select(ImageDatasetSnapshot).where(ImageDatasetSnapshot.state == "frozen")).all():
        if row.id in (snap.example_ids or []):
            snap.state = "invalidated"
    db.flush()


def evaluate_improvement(db: Session, *, dataset: ImageDatasetSnapshot, actor: User, component_type: str, candidate_name: str, artifact_sha256: str, baseline: dict, candidate: dict, max_compute_growth: float = 0.25) -> ImageImprovementRun:
    if dataset.state != "frozen":
        raise ImageLearningError("Dataset snapshot is not eligible for improvement evaluation")
    required = {"quality_score", "artifact_failure_rate", "compute_ms"}
    if not required.issubset(baseline) or not required.issubset(candidate):
        raise ImageLearningError("Holdout metrics require quality_score, artifact_failure_rate and compute_ms")
    quality_ok = float(candidate["quality_score"]) >= float(baseline["quality_score"])
    artifacts_ok = float(candidate["artifact_failure_rate"]) <= float(baseline["artifact_failure_rate"])
    base_compute = max(1.0, float(baseline["compute_ms"]))
    compute_ok = float(candidate["compute_ms"]) <= base_compute * (1.0 + max_compute_growth)
    improved = float(candidate["quality_score"]) > float(baseline["quality_score"]) or float(candidate["artifact_failure_rate"]) < float(baseline["artifact_failure_rate"])
    accepted = quality_ok and artifacts_ok and compute_ok and improved
    reasons = []
    if not quality_ok: reasons.append("quality regression")
    if not artifacts_ok: reasons.append("artifact failure regression")
    if not compute_ok: reasons.append("compute growth exceeds gate")
    if quality_ok and artifacts_ok and compute_ok and not improved: reasons.append("no measured holdout improvement")
    row = ImageImprovementRun(dataset_snapshot_id=dataset.id, component_type=component_type, candidate_name=candidate_name, artifact_sha256=artifact_sha256, baseline_metrics=baseline, candidate_metrics=candidate, state="accepted" if accepted else "rejected", decision_reason=", ".join(reasons) if reasons else "holdout quality improved within resource gate", created_by=actor.id)
    db.add(row); db.flush(); return row
