from pathlib import Path
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BackgroundJob, ImageBlob, ImageGeneration, ImageVariant, User
from app.schemas.images import ImageGenerationCreate, ImageGenerationRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.image_runtime import ImageRuntimeError, ensure_disk_capacity, user_image_storage_bytes, validate_dimensions
from app.services.image_policy import evaluate_prompt, published_policy
from app.services.jobs import enqueue_job
from app.services.safety import require_capability

router=APIRouter(prefix="/v1/images",tags=["images"])

def _access(db:Session,user:User,generation_id:str)->ImageGeneration:
    row=db.get(ImageGeneration,generation_id)
    if row is None: raise HTTPException(status_code=404,detail="Image generation not found")
    if row.project_id: require_project_role(db,user,row.project_id,"viewer")
    elif row.user_id!=user.id: raise HTTPException(status_code=404,detail="Image generation not found")
    return row

@router.post("/generations",response_model=ImageGenerationRead,status_code=status.HTTP_202_ACCEPTED)
def create_generation(payload:ImageGenerationCreate,request:Request,user:User=Depends(get_current_user),db:Session=Depends(get_db))->ImageGeneration:
    require_capability(db,user.id,"images"); settings=request.app.state.settings
    if payload.project_id: require_project_role(db,user,payload.project_id,"member")
    policy=published_policy(db); decision=evaluate_prompt(policy,payload.prompt)
    if not decision.allowed: raise HTTPException(status_code=422,detail={"code":"image_policy_blocked","rule":decision.rule})
    steps=payload.steps or settings.image_default_steps
    if steps>settings.image_max_steps: raise HTTPException(status_code=422,detail="Image step budget exceeded")
    try: validate_dimensions(payload.width,payload.height,max_dimension=settings.image_max_dimension,max_pixels=settings.image_max_pixels)
    except ImageRuntimeError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    if settings.image_backend=="disabled": raise HTTPException(status_code=503,detail="Local image generation is not configured")
    try: ensure_disk_capacity(settings.image_storage_path,min_free_bytes=settings.image_storage_min_free_bytes,min_free_percent=settings.image_storage_min_free_percent)
    except ImageRuntimeError as exc: raise HTTPException(status_code=507,detail=str(exc)) from exc
    if user_image_storage_bytes(db,user.id)>=settings.image_user_storage_quota_bytes: raise HTTPException(status_code=507,detail="User image storage quota reached")
    active=db.scalar(select(func.count(ImageGeneration.id)).where(ImageGeneration.user_id==user.id,ImageGeneration.status.in_(["queued","generating"]))) or 0
    if int(active)>=settings.image_max_active_per_user: raise HTTPException(status_code=429,detail="Too many active image generations")
    seed=payload.seed if payload.seed is not None else secrets.randbelow(2**31)
    generation=ImageGeneration(user_id=user.id,project_id=payload.project_id,prompt=payload.prompt.strip(),negative_prompt=payload.negative_prompt.strip(),status="queued",backend=settings.image_backend,model_name=settings.image_model_name,width=payload.width,height=payload.height,steps=steps,seed=seed,safety_policy_id=policy.id if policy else None,safety_status="allowed" if policy else "no_policy",delivery_status="active"); db.add(generation); db.flush()
    job=enqueue_job(db,kind="image.generate",payload={"generation_id":generation.id},user_id=user.id,project_id=payload.project_id,priority=settings.image_job_priority,max_attempts=2,idempotency_key=f"image:{generation.id}"); generation.job_id=job.id; generation.manifest={"requested_backend":settings.image_backend,"requested_model":settings.image_model_name,"seed":seed,"steps":steps,"width":payload.width,"height":payload.height,"safety_policy_id":policy.id if policy else None,"safety_policy_version":policy.version if policy else None}; db.commit(); db.refresh(generation); return generation

@router.get("/status")
def image_status(request:Request,user:User=Depends(get_current_user),db:Session=Depends(get_db))->dict:
    settings=request.app.state.settings; active=int(db.scalar(select(func.count(ImageGeneration.id)).where(ImageGeneration.user_id==user.id,ImageGeneration.status.in_(["queued","generating"]))) or 0); used=user_image_storage_bytes(db,user.id); configured=settings.image_backend!="disabled"; quota_ok=used<settings.image_user_storage_quota_bytes; slot_ok=active<settings.image_max_active_per_user; reason=None
    if not configured: reason="local_image_backend_not_configured"
    elif not quota_ok: reason="storage_quota_reached"
    elif not slot_ok: reason="active_generation_limit"
    return {"available":configured and quota_ok and slot_ok,"reason":reason,"backend":settings.image_backend if configured else "disabled","active_generations":active,"max_active_generations":settings.image_max_active_per_user,"storage_used_bytes":used,"storage_quota_bytes":settings.image_user_storage_quota_bytes,"queue_waiting":int(getattr(request.app.state.governor,"waiting",0))}

@router.get("/generations",response_model=list[ImageGenerationRead])
def list_generations(user:User=Depends(get_current_user),db:Session=Depends(get_db))->list[ImageGeneration]: return list(db.scalars(select(ImageGeneration).where(ImageGeneration.user_id==user.id).order_by(ImageGeneration.created_at.desc()).limit(100)).all())
@router.get("/generations/{generation_id}",response_model=ImageGenerationRead)
def get_generation(generation_id:str,user:User=Depends(get_current_user),db:Session=Depends(get_db))->ImageGeneration:return _access(db,user,generation_id)
@router.post("/generations/{generation_id}/cancel",response_model=ImageGenerationRead)
def cancel_generation(generation_id:str,user:User=Depends(get_current_user),db:Session=Depends(get_db))->ImageGeneration:
    row=_access(db,user,generation_id)
    if row.user_id!=user.id and row.project_id: require_project_role(db,user,row.project_id,"manager")
    if row.status=="queued":
        row.status="cancelled"
        if row.job_id:
            job=db.get(BackgroundJob,row.job_id)
            if job and job.status=="queued": job.status="cancelled"
        db.commit(); db.refresh(row); return row
    if row.status=="generating": raise HTTPException(status_code=409,detail="Generation is already running; cooperative cancellation is not available for this backend yet")
    return row
@router.get("/generations/{generation_id}/content")
def image_content(generation_id:str,variant:str=Query(default="preferred",pattern="^(preferred|source|preview)$"),user:User=Depends(get_current_user),db:Session=Depends(get_db))->Response:
    row=_access(db,user,generation_id)
    if row.delivery_status!="active": raise HTTPException(status_code=451 if row.delivery_status=="revoked" else 423,detail="Image delivery is not available")
    if row.status!="ready" or row.qa_status!="passed" or not row.blob_id: raise HTTPException(status_code=409,detail="Image has not passed quality assurance")
    blob_id=row.blob_id if variant=="source" else (row.preferred_blob_id or row.blob_id)
    if variant=="preview":
        preview=db.scalar(select(ImageVariant).where(ImageVariant.source_blob_id==row.blob_id,ImageVariant.kind=="preview_webp"))
        if preview is not None: blob_id=preview.blob_id
    blob=db.get(ImageBlob,blob_id)
    if blob is None: raise HTTPException(status_code=500,detail="Image blob missing")
    path=Path(blob.storage_path)
    if not path.is_file(): raise HTTPException(status_code=500,detail="Image file missing")
    return Response(content=path.read_bytes(),media_type=blob.media_type,headers={"ETag":blob.sha256,"Cache-Control":"private, max-age=31536000, immutable","X-X1-Image-Variant":variant})

from pydantic import BaseModel, Field
from app.models import ImageFeedback
from app.services.image_learning import build_candidate, withdraw_training_consent
class ImageFeedbackWrite(BaseModel): rating:int=Field(ge=1,le=5); allow_training:bool=False
@router.post("/generations/{generation_id}/feedback",status_code=status.HTTP_204_NO_CONTENT)
def image_feedback(generation_id:str,payload:ImageFeedbackWrite,user:User=Depends(get_current_user),db:Session=Depends(get_db))->Response:
    row=_access(db,user,generation_id)
    if row.user_id!=user.id: raise HTTPException(status_code=403,detail="Only the generation owner can submit training consent")
    fb=db.scalar(select(ImageFeedback).where(ImageFeedback.generation_id==row.id,ImageFeedback.user_id==user.id))
    if fb is None: fb=ImageFeedback(generation_id=row.id,user_id=user.id,rating=payload.rating,allow_training=payload.allow_training); db.add(fb)
    else: fb.rating=payload.rating; fb.allow_training=payload.allow_training
    db.flush(); build_candidate(db,row,fb) if payload.allow_training else withdraw_training_consent(db,row.id,user.id); db.commit(); return Response(status_code=status.HTTP_204_NO_CONTENT)
