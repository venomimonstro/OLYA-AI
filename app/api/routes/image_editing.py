from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BackgroundJob, ImageBlob, ImageEditRequest, ImageGeneration, ImageReference, User, utcnow
from app.schemas.image_editing import ImageEditCreate, ImageEditCreateResponse, ImageEditRead, ImageReferenceRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.image_editing import ImageEditError, resolve_edit_mode
from app.services.image_policy import evaluate_prompt, published_policy
from app.services.image_references import ImageReferenceError, store_reference, total_user_image_storage_bytes
from app.services.image_runtime import ImageRuntimeError, ensure_disk_capacity
from app.services.jobs import enqueue_job
from app.services.safety import require_capability

router = APIRouter(tags=["image-editing"])
_LOCAL_EDIT_MODES = {"remove_object", "replace_object", "add_object", "background"}


def _reference_access(db: Session, user: User, reference_id: str, minimum_project_role: str = "viewer") -> ImageReference:
    row = db.get(ImageReference, reference_id)
    if row is None or row.status != "ready":
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


async def _read_limited_image(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            size = int(declared)
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
        code = 507 if "quota" in message.lower() or "disk" in message.lower() or "storage" in message.lower() else 422
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
def reference_content(reference_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    reference = _reference_access(db, user, reference_id)
    blob = db.get(ImageBlob, reference.blob_id)
    if blob is None:
        raise HTTPException(status_code=410, detail="Reference image blob is unavailable")
    path = Path(blob.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Reference image content is unavailable")
    return FileResponse(path=path, media_type=blob.media_type, headers={"Cache-Control": "private, no-store", "ETag": blob.sha256})


@router.delete("/references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference(reference_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    reference = _reference_access(db, user, reference_id)
    if reference.project_id:
        if reference.user_id != user.id:
            require_project_role(db, user, reference.project_id, "manager")
    elif reference.user_id != user.id:
        raise HTTPException(status_code=404, detail="Image reference not found")
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
    reference.status = "deleted"
    reference.deleted_at = utcnow()
    db.commit()
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
def create_edit(
    payload: ImageEditCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImageEditCreateResponse:
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
            raise HTTPException(status_code=422, detail="identity_reference_id is not an identity-capable reference")
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
    if str(settings.image_edit_backend).lower() == "disabled":
        raise HTTPException(status_code=503, detail="Local image editing is not configured")
    has_local_model = bool(str(settings.image_edit_model_path).strip())
    has_identity_model = bool(str(settings.image_edit_identity_model_path).strip())
    has_vision = bool(str(settings.image_vision_qa_url).strip())
    if not has_local_model and not has_identity_model:
        raise HTTPException(status_code=503, detail="No local image editing model is configured")
    if resolved_mode == "identity_recompose" and not has_identity_model:
        raise HTTPException(status_code=503, detail="Identity-preserving scene editing model is not configured")
    if resolved_mode in _LOCAL_EDIT_MODES and not has_local_model:
        raise HTTPException(status_code=503, detail="Local inpainting model is not configured")
    if resolved_mode == "identity_recompose" and payload.preserve_identity and not has_vision:
        raise HTTPException(status_code=503, detail="Identity-preserving scene editing requires local vision QA")
    if payload.strict_quality and settings.image_edit_require_vision_qa and not has_vision:
        raise HTTPException(status_code=503, detail="Strict image editing requires local vision QA")
    if payload.strict_quality and resolved_mode in {"remove_object", "replace_object", "add_object"} and not payload.preserve_outside_mask:
        raise HTTPException(status_code=422, detail="Strict local object editing requires preserve_outside_mask=true")
    if resolved_mode == "auto" and not payload.mask_reference_id and not has_vision:
        raise HTTPException(status_code=503, detail="Automatic edit intent/object localization requires local vision QA or an explicit edit mode with a mask")

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
def list_edits(
    project_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ImageEditRequest]:
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


@router.get("/studio", response_class=HTMLResponse, include_in_schema=False)
def image_studio() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    html = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OLYA AI — редактор фото</title>
<style nonce="__NONCE__">:root{color-scheme:dark;--bg:#090b10;--card:#131821;--line:#2a3140;--text:#f4f6fa;--muted:#9aa5b4;--accent:#d34747;--ok:#69d69a;--bad:#ff8383}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,Arial,sans-serif}.wrap{max-width:1100px;margin:auto;padding:28px 18px 50px}h1{font-size:34px;margin:0 0 6px}.lead{color:var(--muted);margin:0 0 24px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}.drop{display:flex;align-items:center;justify-content:center;min-height:310px;border:1px dashed #465065;border-radius:13px;overflow:hidden;position:relative}.drop img,.result img{max-width:100%;max-height:520px;display:block}.drop input{position:absolute;inset:0;opacity:0;cursor:pointer}.placeholder{color:var(--muted);text-align:center;padding:30px}textarea{width:100%;min-height:110px;background:#0d1118;color:#fff;border:1px solid var(--line);border-radius:12px;padding:12px;resize:vertical;margin-top:12px}button{border:0;border-radius:10px;padding:11px 15px;background:var(--accent);color:white;font-weight:700;cursor:pointer}button:disabled{opacity:.5;cursor:default}.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}.state{margin-top:12px;color:var(--muted);white-space:pre-wrap}.state.bad{color:var(--bad)}.state.ok{color:var(--ok)}.result{min-height:310px;display:flex;align-items:center;justify-content:center;background:#0d1118;border-radius:13px;overflow:hidden}.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:4px 8px;color:var(--muted)}label{color:#dfe5ef}@media(max-width:760px){.grid{grid-template-columns:1fr}.wrap{padding:18px 10px}h1{font-size:28px}}</style></head><body><div class="wrap"><h1>Редактор фото</h1><p class="lead">Загрузите фотографию и напишите, что нужно изменить. Локальные правки защищают пиксели вне редактируемой области; изменения человека проходят отдельную проверку сходства.</p><div class="grid"><section class="card"><div class="drop" id="drop"><div class="placeholder" id="ph">Нажмите или перетащите JPG/PNG/WebP<br>Исходник останется локальным в OLYA AI</div><img id="source" hidden alt="Исходное фото"><input id="file" type="file" accept="image/png,image/jpeg,image/webp"></div><textarea id="instruction" maxlength="6000" placeholder="Например: убери корабль на заднем фоне"></textarea><div class="row"><label><input id="strict" type="checkbox" checked> Строгая проверка</label><span class="pill">Auto edit</span><button id="run" disabled>Изменить фото</button></div><div class="state" id="state">Проверяем доступность редактора…</div></section><section class="card"><div class="result" id="result"><div class="placeholder">Здесь появится только изображение, прошедшее QA</div></div><div class="state" id="qa"></div></section></div></div>
<script nonce="__NONCE__">const token=sessionStorage.getItem('x1_access_token');if(!token){location.replace('/login')}const $=id=>document.getElementById(id);let sourceRef=null,sourceUrl=null,resultUrl=null,busy=false;const auth=()=>({Authorization:'Bearer '+token});function detail(d,s){if(d&&typeof d.detail==='string')return d.detail;if(d&&d.detail&&d.detail.message)return d.detail.message;return s===503?'Редактор изображений пока не настроен на этом сервере.':'Операция не выполнена.'}async function json(path,opt={}){const h={...auth(),...(opt.headers||{})};if(opt.body&&typeof opt.body==='string')h['Content-Type']='application/json';const r=await fetch(path,{...opt,headers:h,credentials:'omit'});let d=null;try{d=await r.json()}catch{}if(r.status===401){sessionStorage.clear();location.replace('/login');throw new Error('Сессия завершена')}if(!r.ok)throw new Error(detail(d,r.status));return d}async function blob(path){const r=await fetch(path,{headers:auth(),credentials:'omit'});if(!r.ok)throw new Error('Не удалось загрузить изображение');return r.blob()}function status(t,kind=''){const e=$('state');e.textContent=t;e.className='state '+kind}async function readiness(){try{const d=await json('/v1/images/status');const e=d.editing||{};if(!e.available){status('Редактор пока недоступен: '+(e.strict_quality_ready===false?'не настроена локальная проверка качества':'не настроена локальная модель редактирования'),'bad')}else status('Готово. Для локальных правок исходные пиксели вне области редактирования будут сохранены.','ok')}catch(e){status(e.message,'bad')}}async function upload(file){busy=true;$('run').disabled=true;status('Загружаем и нормализуем исходник…');try{const q=new URLSearchParams({filename:file.name,kind:'edit_source'});const r=await fetch('/v1/images/references?'+q,{method:'POST',headers:{...auth(),'Content-Type':file.type||'application/octet-stream'},body:file,credentials:'omit'});let d=null;try{d=await r.json()}catch{}if(!r.ok)throw new Error(detail(d,r.status));sourceRef=d;if(sourceUrl)URL.revokeObjectURL(sourceUrl);sourceUrl=URL.createObjectURL(file);$('source').src=sourceUrl;$('source').hidden=false;$('ph').hidden=true;status('Фото загружено. Опишите изменение.','ok')}catch(e){sourceRef=null;status(e.message,'bad')}finally{busy=false;$('run').disabled=!sourceRef}}$('file').addEventListener('change',e=>{const f=e.target.files&&e.target.files[0];if(f)upload(f)});$('instruction').addEventListener('input',()=>{$('run').disabled=busy||!sourceRef||!$('instruction').value.trim()});async function poll(editId,generationId){for(let i=0;i<600;i++){await new Promise(r=>setTimeout(r,1500));const edit=await json('/v1/images/edits/'+editId);status('Статус: '+edit.status+(edit.status==='generating'?' — выполняется локальная генерация и QA':''));if(edit.status==='failed')throw new Error(edit.error_message||'Результат отклонён проверкой качества');if(edit.status==='cancelled')throw new Error('Редактирование отменено');if(edit.status==='ready'){const b=await blob('/v1/images/generations/'+generationId+'/content?variant=preferred');if(resultUrl)URL.revokeObjectURL(resultUrl);resultUrl=URL.createObjectURL(b);const img=document.createElement('img');img.src=resultUrl;img.alt='Отредактированное фото';$('result').replaceChildren(img);const q=edit.qa_summary||{};$('qa').textContent='QA: пройдено. Режим: '+(q.mode||'edit')+', попыток: '+(q.attempts||1)+'.';$('qa').className='state ok';status('Готово — показан только QA-прошедший результат.','ok');return}}throw new Error('Редактирование превысило безопасное время ожидания')}$('run').onclick=async()=>{if(busy||!sourceRef)return;const instruction=$('instruction').value.trim();if(!instruction)return;busy=true;$('run').disabled=true;$('qa').textContent='';$('result').innerHTML='<div class="placeholder">Редактируем и проверяем…</div>';try{const r=await json('/v1/images/edits',{method:'POST',body:JSON.stringify({source_reference_id:sourceRef.id,instruction,mode:'auto',preserve_identity:true,preserve_outside_mask:true,strict_quality:$('strict').checked})});status('Задача принята. Планируем область изменения…');await poll(r.edit.id,r.generation_id)}catch(e){status(e.message,'bad');$('qa').textContent='Результат не выдан: проверка или выполнение не завершились успешно.';$('qa').className='state bad'}finally{busy=false;$('run').disabled=!sourceRef||!$('instruction').value.trim()}};readiness();</script></body></html>'''.replaceAll("__NONCE__", nonce)
    return HTMLResponse(html, headers={"Content-Security-Policy": f"default-src 'self'; img-src 'self' blob: data:; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'", "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow, noarchive"})
