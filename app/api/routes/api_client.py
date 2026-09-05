from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.chat import chat as chat_handler
from app.db import get_db
from app.models import ApiKey, ApiRequestTelemetry, Conversation, PersistentApiContext, User
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.commerce import ApiChatRequest, ApiContextCreate, ApiContextRead
from app.services.access import require_project_role
from app.services.api_access import require_api_scope
from app.services.commerce import ensure_organization_budget, price_resource_ms, record_telemetry

router = APIRouter(prefix="/v1/api", tags=["api-client"])


def _context_access(db: Session, key: ApiKey, user: User, context_id: str) -> PersistentApiContext:
    row = db.get(PersistentApiContext, context_id)
    if row is None or row.owner_id != user.id or row.organization_id != key.organization_id:
        raise HTTPException(status_code=404, detail="API context not found")
    return row


@router.post("/contexts", response_model=ApiContextRead, status_code=status.HTTP_201_CREATED)
def create_context(payload: ApiContextCreate, principal=Depends(require_api_scope("contexts:write")), db: Session = Depends(get_db)):
    key, user = principal
    project_id = payload.project_id
    conversation_id = payload.conversation_id
    if project_id:
        require_project_role(db, user, project_id, "member")
    if conversation_id:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if project_id and conv.project_id != project_id:
            raise HTTPException(status_code=409, detail="Conversation/project mismatch")
        if conv.project_id:
            require_project_role(db, user, conv.project_id, "viewer"); project_id = conv.project_id
        elif conv.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")
    row = PersistentApiContext(owner_id=user.id, organization_id=key.organization_id, project_id=project_id,
                               conversation_id=conversation_id, label=payload.label, metadata_json=payload.metadata)
    db.add(row); db.commit(); db.refresh(row)
    return ApiContextRead(id=row.id, project_id=row.project_id, conversation_id=row.conversation_id, label=row.label,
                          metadata=row.metadata_json, created_at=row.created_at, updated_at=row.updated_at)


@router.get("/contexts/{context_id}", response_model=ApiContextRead)
def get_context(context_id: str, principal=Depends(require_api_scope("contexts:read")), db: Session = Depends(get_db)):
    key, user = principal; row = _context_access(db, key, user, context_id); db.commit()
    return ApiContextRead(id=row.id, project_id=row.project_id, conversation_id=row.conversation_id, label=row.label,
                          metadata=row.metadata_json, created_at=row.created_at, updated_at=row.updated_at)


@router.post("/chat", response_model=ChatResponse)
async def api_chat(payload: ApiChatRequest, request: Request, principal=Depends(require_api_scope("chat")), db: Session = Depends(get_db)):
    key, user = principal
    context = None
    if payload.context_id:
        context = _context_access(db, key, user, payload.context_id)
    if key.organization_id:
        reserve_seconds = {"fast":15,"work":60,"deep":180}.get(payload.mode, 60)
        if payload.verification == "strict" or (payload.verification == "auto" and payload.requirements):
            reserve_seconds *= 2
        reserve_cost = price_resource_ms(request.app.state.settings, "cpu", reserve_seconds * 1000)
        try:
            ensure_organization_budget(db, key.organization_id, reserve_cost)
        except RuntimeError as exc:
            db.rollback(); raise HTTPException(status_code=429, detail=str(exc)) from exc
    data = payload.model_dump(exclude={"context_id"})
    if context:
        data["project_id"] = context.project_id
        data["conversation_id"] = context.conversation_id
    chat_payload = ChatRequest.model_validate(data)
    started = perf_counter(); external_request_id = uuid4().hex
    try:
        response = await chat_handler(chat_payload, request, user, db)
    except HTTPException as exc:
        usage = getattr(request.state, "x1_usage", {}) or {}
        cpu_ms = int(usage.get("cpu_ms", 0)); cost = price_resource_ms(request.app.state.settings, "cpu", cpu_ms)
        record_telemetry(db, api_key=key, endpoint="/v1/api/chat", request_id=external_request_id, status_code=exc.status_code,
                         latency_ms=int((perf_counter()-started)*1000), quality_status="failed", context_id=context.id if context else None,
                         project_id=context.project_id if context else chat_payload.project_id,
                         resource_usage={"cpu_ms":cpu_ms}, cost_microunits=cost)
        db.commit(); raise
    if context and context.conversation_id is None:
        context.conversation_id = getattr(request.state, "x1_conversation_id", None)
    usage = getattr(request.state, "x1_usage", {}) or {}
    cpu_ms = int(usage.get("cpu_ms", 0)); cost = price_resource_ms(request.app.state.settings, "cpu", cpu_ms)
    quality_status = response.quality.status if response.quality else "unchecked"
    record_telemetry(db, api_key=key, endpoint="/v1/api/chat", request_id=external_request_id, status_code=200,
                     latency_ms=int((perf_counter()-started)*1000), quality_status=quality_status,
                     context_id=context.id if context else None, project_id=context.project_id if context else chat_payload.project_id,
                     resource_usage={"cpu_ms":cpu_ms}, cost_microunits=cost)
    db.commit()
    return response


@router.get("/telemetry")
def telemetry(limit: int = Query(default=100, ge=1, le=500), principal=Depends(require_api_scope("telemetry:read")),
              db: Session = Depends(get_db)) -> list[dict]:
    key, _user = principal
    rows = db.scalars(select(ApiRequestTelemetry).where(ApiRequestTelemetry.api_key_id == key.id)
                      .order_by(ApiRequestTelemetry.created_at.desc()).limit(limit)).all()
    db.commit()
    return [{"request_id":x.request_id,"endpoint":x.endpoint,"status_code":x.status_code,"latency_ms":x.latency_ms,
             "quality_status":x.quality_status,"cost_microunits":x.cost_microunits,"resource_usage":x.resource_usage,
             "context_id":x.context_id,"project_id":x.project_id,"created_at":x.created_at} for x in rows]
