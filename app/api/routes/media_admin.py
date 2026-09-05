from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ImageBlob, ImageGeneration, ImagePolicyTestCase, ImageQAEvent, ImageSafetyPolicy, ImageVariant, User
from app.services.admin import audit, require_admin
from app.services.image_policy import ImagePolicyError, evaluate_prompt, next_policy_version, publish_policy, run_policy_regression, validate_rules

router = APIRouter(prefix="/v1/admin/media", tags=["admin-media"])


class PolicyCreate(BaseModel):
    name: str = Field(default="Image Safety Policy", min_length=1, max_length=160)
    superprompt: str = Field(default="", max_length=12000)
    rules: dict = Field(default_factory=dict)


class PolicyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    superprompt: str | None = Field(default=None, max_length=12000)
    rules: dict | None = None


class PolicyTestCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)
    expected: Literal["allow", "block"]


class ModerationAction(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


def _generation_summary(g: ImageGeneration) -> dict:
    return {
        "id": g.id, "user_id": g.user_id, "project_id": g.project_id, "status": g.status,
        "qa_status": g.qa_status, "safety_status": g.safety_status, "delivery_status": g.delivery_status,
        "backend": g.backend, "model_name": g.model_name, "width": g.width, "height": g.height,
        "repair_attempts": g.repair_attempts, "created_at": g.created_at, "finished_at": g.finished_at,
        "safety_policy_id": g.safety_policy_id,
    }


@router.get("/summary")
def media_summary(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    del admin
    total = int(db.scalar(select(func.count()).select_from(ImageGeneration)) or 0)
    ready = int(db.scalar(select(func.count()).select_from(ImageGeneration).where(ImageGeneration.status == "ready")) or 0)
    qa_failed = int(db.scalar(select(func.count()).select_from(ImageGeneration).where(ImageGeneration.status == "qa_failed")) or 0)
    quarantined = int(db.scalar(select(func.count()).select_from(ImageGeneration).where(ImageGeneration.delivery_status == "quarantined")) or 0)
    revoked = int(db.scalar(select(func.count()).select_from(ImageGeneration).where(ImageGeneration.delivery_status == "revoked")) or 0)
    storage = int(db.scalar(select(func.coalesce(func.sum(ImageBlob.size_bytes), 0))) or 0)
    variants = int(db.scalar(select(func.count()).select_from(ImageVariant)) or 0)
    timed = db.scalars(select(ImageGeneration).where(ImageGeneration.started_at.is_not(None), ImageGeneration.finished_at.is_not(None)).order_by(ImageGeneration.finished_at.desc()).limit(500)).all()
    durations = [(g.finished_at - g.started_at).total_seconds() * 1000 for g in timed if g.finished_at and g.started_at]
    avg_latency_ms = round(sum(durations) / len(durations), 2) if durations else None
    top = db.execute(
        select(ImageGeneration.user_id, func.count(ImageGeneration.id).label("generations"))
        .group_by(ImageGeneration.user_id).order_by(func.count(ImageGeneration.id).desc()).limit(10)
    ).all()
    findings = db.execute(select(ImageQAEvent.status, func.count(ImageQAEvent.id)).group_by(ImageQAEvent.status)).all()
    return {
        "generations": total, "ready": ready, "qa_failed": qa_failed, "quarantined": quarantined, "revoked": revoked,
        "storage_bytes": storage, "variant_count": variants, "avg_generation_latency_ms": avg_latency_ms,
        "top_users": [{"user_id": u, "generations": int(c)} for u, c in top],
        "qa_events": {str(k): int(v) for k, v in findings},
    }


@router.get("/generations")
def list_media(status_filter: str | None = Query(default=None, alias="status"), delivery_status: str | None = None, limit: int = Query(default=100, ge=1, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    del admin
    stmt = select(ImageGeneration).order_by(ImageGeneration.created_at.desc()).limit(limit)
    if status_filter: stmt = stmt.where(ImageGeneration.status == status_filter)
    if delivery_status: stmt = stmt.where(ImageGeneration.delivery_status == delivery_status)
    return [_generation_summary(x) for x in db.scalars(stmt).all()]


@router.get("/generations/{generation_id}")
def generation_detail(generation_id: str, x_admin_access_reason: str = Header(default=""), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    if len(x_admin_access_reason.strip()) < 8:
        raise HTTPException(status_code=400, detail="Concrete admin access reason is required")
    row = db.get(ImageGeneration, generation_id)
    if row is None: raise HTTPException(status_code=404, detail="Image generation not found")
    events = db.scalars(select(ImageQAEvent).where(ImageQAEvent.generation_id == row.id).order_by(ImageQAEvent.created_at)).all()
    audit(db, admin, "media.inspect", "image_generation", row.id, {"reason": x_admin_access_reason.strip()[:500]})
    db.commit()
    return {**_generation_summary(row), "prompt": row.prompt, "negative_prompt": row.negative_prompt, "manifest": row.manifest,
            "moderation_note": row.moderation_note,
            "qa_events": [{"id": e.id, "type": e.qa_type, "status": e.status, "findings": e.findings, "metrics": e.metrics, "created_at": e.created_at} for e in events]}


@router.get("/generations/{generation_id}/content")
def generation_content(generation_id: str, x_admin_access_reason: str = Header(default=""), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> Response:
    if len(x_admin_access_reason.strip()) < 8:
        raise HTTPException(status_code=400, detail="Concrete admin access reason is required")
    row = db.get(ImageGeneration, generation_id)
    if row is None or not row.blob_id: raise HTTPException(status_code=404, detail="Image content not found")
    blob = db.get(ImageBlob, row.preferred_blob_id or row.blob_id)
    if blob is None or not Path(blob.storage_path).is_file(): raise HTTPException(status_code=404, detail="Image content not found")
    audit(db, admin, "media.content_access", "image_generation", row.id, {"reason": x_admin_access_reason.strip()[:500]})
    db.commit()
    return Response(Path(blob.storage_path).read_bytes(), media_type=blob.media_type, headers={"Cache-Control": "no-store"})


@router.post("/generations/{generation_id}/quarantine", status_code=204)
def quarantine(generation_id: str, payload: ModerationAction, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(ImageGeneration, generation_id)
    if row is None: raise HTTPException(status_code=404, detail="Image generation not found")
    row.delivery_status = "quarantined"; row.moderation_note = payload.note
    audit(db, admin, "media.quarantine", "image_generation", row.id, {"note": payload.note})
    db.commit()


@router.post("/generations/{generation_id}/restore", status_code=204)
def restore(generation_id: str, payload: ModerationAction, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(ImageGeneration, generation_id)
    if row is None: raise HTTPException(status_code=404, detail="Image generation not found")
    if row.delivery_status == "revoked": raise HTTPException(status_code=409, detail="Revoked media cannot be restored")
    row.delivery_status = "active"; row.moderation_note = payload.note
    audit(db, admin, "media.restore", "image_generation", row.id, {"note": payload.note})
    db.commit()


@router.post("/generations/{generation_id}/revoke", status_code=204)
def revoke(generation_id: str, payload: ModerationAction, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(ImageGeneration, generation_id)
    if row is None: raise HTTPException(status_code=404, detail="Image generation not found")
    row.delivery_status = "revoked"; row.moderation_note = payload.note
    audit(db, admin, "media.revoke", "image_generation", row.id, {"note": payload.note})
    db.commit()


@router.get("/policies")
def policies(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    del admin
    rows = db.scalars(select(ImageSafetyPolicy).order_by(ImageSafetyPolicy.version.desc())).all()
    return [{"id": p.id, "version": p.version, "name": p.name, "state": p.state, "superprompt": p.superprompt, "rules": p.rules, "created_by": p.created_by, "published_by": p.published_by, "created_at": p.created_at, "published_at": p.published_at} for p in rows]


@router.post("/policies", status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    try: rules = validate_rules(payload.rules)
    except ImagePolicyError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = ImageSafetyPolicy(version=next_policy_version(db), state="draft", name=payload.name, superprompt=payload.superprompt.strip(), rules=rules, created_by=admin.id)
    db.add(row); db.flush(); audit(db, admin, "image_policy.create", "image_safety_policy", row.id, {"version": row.version}); db.commit(); db.refresh(row)
    return {"id": row.id, "version": row.version, "state": row.state}


@router.patch("/policies/{policy_id}")
def patch_policy(policy_id: str, payload: PolicyPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    if row.state != "draft": raise HTTPException(status_code=409, detail="Only draft policy can be edited")
    if payload.name is not None: row.name = payload.name
    if payload.superprompt is not None: row.superprompt = payload.superprompt.strip()
    if payload.rules is not None:
        try: row.rules = validate_rules(payload.rules)
        except ImagePolicyError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(db, admin, "image_policy.update", "image_safety_policy", row.id, {"version": row.version}); db.commit()
    return {"id": row.id, "version": row.version, "state": row.state}


@router.post("/policies/{policy_id}/tests", status_code=status.HTTP_201_CREATED)
def add_policy_test(policy_id: str, payload: PolicyTestCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    test = ImagePolicyTestCase(policy_id=row.id, prompt=payload.prompt.strip(), expected=payload.expected)
    db.add(test); db.flush(); audit(db, admin, "image_policy.test_add", "image_safety_policy", row.id, {"test_id": test.id, "expected": test.expected}); db.commit(); return {"id": test.id}


@router.post("/policies/{policy_id}/test")
def test_policy(policy_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    del admin
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    results = run_policy_regression(db, row)
    return {"passed": all(x["passed"] for x in results), "results": results}


@router.post("/policies/{policy_id}/stage", status_code=204)
def stage_policy(policy_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    if row.state != "draft": raise HTTPException(status_code=409, detail="Only draft policy can be staged")
    row.state = "staging"; audit(db, admin, "image_policy.stage", "image_safety_policy", row.id, {"version": row.version}); db.commit()


@router.post("/policies/{policy_id}/publish")
def publish(policy_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    try: results = publish_policy(db, row, admin)
    except ImagePolicyError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, "image_policy.publish", "image_safety_policy", row.id, {"version": row.version}); db.commit()
    return {"id": row.id, "version": row.version, "state": row.state, "tests": results}


@router.post("/policies/{policy_id}/rollback")
def rollback(policy_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(ImageSafetyPolicy, policy_id)
    if row is None: raise HTTPException(status_code=404, detail="Image policy not found")
    if row.state not in {"archived", "published"}: raise HTTPException(status_code=409, detail="Only a previously published policy can be restored")
    try: results = publish_policy(db, row, admin)
    except ImagePolicyError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, "image_policy.rollback", "image_safety_policy", row.id, {"version": row.version}); db.commit()
    return {"id": row.id, "version": row.version, "state": row.state, "tests": results}

from app.models import ImageDatasetSnapshot, ImageTrainingExample
from app.services.image_learning import ImageLearningError, freeze_dataset, refresh_candidates


class TrainingDecision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


@router.post("/training/refresh")
def refresh_training_candidates(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    created = refresh_candidates(db)
    audit(db, admin, action="media.training.refresh", target_type="image_training", target_id=None, details={"created": created})
    db.commit()
    return {"created": created}


@router.get("/training/examples")
def list_training_examples(state_filter: str | None = Query(default=None, alias="state"), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    del admin
    stmt = select(ImageTrainingExample).order_by(ImageTrainingExample.created_at.desc()).limit(500)
    if state_filter:
        stmt = stmt.where(ImageTrainingExample.state == state_filter)
    rows = db.scalars(stmt).all()
    return [{"id": r.id, "generation_id": r.generation_id, "label": r.label, "state": r.state, "dedupe_key": r.dedupe_key, "review_note": r.review_note, "created_at": r.created_at, "reviewed_at": r.reviewed_at} for r in rows]


@router.post("/training/examples/{example_id}/decision", status_code=status.HTTP_204_NO_CONTENT)
def training_decision(example_id: str, payload: TrainingDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> Response:
    row = db.get(ImageTrainingExample, example_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Training example not found")
    if row.state == "invalidated":
        raise HTTPException(status_code=409, detail="Training example is no longer eligible")
    row.state = "approved" if payload.decision == "approve" else "rejected"
    row.reviewed_by = admin.id
    row.review_note = payload.note
    row.reviewed_at = datetime.now(timezone.utc)
    audit(db, admin, action="media.training.decision", target_type="image_training_example", target_id=row.id, details={"decision": payload.decision, "label": row.label})
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/training/datasets", status_code=status.HTTP_201_CREATED)
def create_training_dataset(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    try:
        row = freeze_dataset(db, admin)
    except ImageLearningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, action="media.training.dataset.freeze", target_type="image_dataset_snapshot", target_id=row.id, details={"sha256": row.manifest_sha256, "positive_count": row.positive_count, "regression_count": row.regression_count})
    db.commit()
    return {"id": row.id, "manifest_sha256": row.manifest_sha256, "positive_count": row.positive_count, "regression_count": row.regression_count, "state": row.state}


@router.get("/training/datasets")
def list_training_datasets(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    del admin
    rows = db.scalars(select(ImageDatasetSnapshot).order_by(ImageDatasetSnapshot.created_at.desc()).limit(100)).all()
    return [{"id": r.id, "manifest_sha256": r.manifest_sha256, "positive_count": r.positive_count, "regression_count": r.regression_count, "state": r.state, "created_at": r.created_at} for r in rows]

from app.models import ImageImprovementRun
from app.services.image_learning import evaluate_improvement


class ImprovementEvaluate(BaseModel):
    dataset_snapshot_id: str
    component_type: Literal["prompt_adapter", "lora", "qa_ranker"]
    candidate_name: str = Field(min_length=1, max_length=160)
    artifact_sha256: str = Field(default="", max_length=64)
    baseline_metrics: dict
    candidate_metrics: dict


@router.post("/training/improvements", status_code=status.HTTP_201_CREATED)
def evaluate_training_improvement(payload: ImprovementEvaluate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    dataset = db.get(ImageDatasetSnapshot, payload.dataset_snapshot_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset snapshot not found")
    try:
        row = evaluate_improvement(db, dataset=dataset, actor=admin, component_type=payload.component_type, candidate_name=payload.candidate_name, artifact_sha256=payload.artifact_sha256, baseline=payload.baseline_metrics, candidate=payload.candidate_metrics)
    except ImageLearningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, action="media.training.improvement.evaluate", target_type="image_improvement_run", target_id=row.id, details={"state": row.state, "component_type": row.component_type})
    db.commit()
    return {"id": row.id, "state": row.state, "decision_reason": row.decision_reason, "component_type": row.component_type, "candidate_name": row.candidate_name}


@router.get("/training/improvements")
def list_training_improvements(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    del admin
    rows = db.scalars(select(ImageImprovementRun).order_by(ImageImprovementRun.created_at.desc()).limit(100)).all()
    return [{"id": r.id, "dataset_snapshot_id": r.dataset_snapshot_id, "component_type": r.component_type, "candidate_name": r.candidate_name, "state": r.state, "decision_reason": r.decision_reason, "created_at": r.created_at} for r in rows]
