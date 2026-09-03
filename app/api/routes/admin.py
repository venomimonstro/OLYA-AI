from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import AdminAuditLog, AnswerAudit, AuthSession, BackgroundJob, FrustrationEvent, OptimizationExperiment, PerformanceSnapshot, RiskEvent, SafetyCase, SearchProviderStat, SystemSetting, UsageEvent, User, UserQuota
from app.services.admin import audit, require_admin
from app.services.auth import get_current_user
from app.services.diagnostics import summary as frustration_summary
from app.services.performance import build_snapshot, evaluate_candidate

router = APIRouter(prefix="/v1/admin", tags=["admin"])

class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    plan: str | None = Field(default=None, max_length=24)
    monthly_compute_seconds_limit: int | None = Field(default=None, ge=0, le=31_536_000)
    max_concurrent_inference: int | None = Field(default=None, ge=1, le=32)
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=32)

class SettingPatch(BaseModel):
    value: dict

class OptimizationExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    baseline: dict
    candidate: dict

@router.post('/bootstrap', status_code=204)
def bootstrap_admin(request: Request, x_admin_bootstrap_token: str = Header(default=''), user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    if db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))):
        raise HTTPException(status_code=409, detail='Administrator already exists')
    expected = request.app.state.settings.admin_bootstrap_token
    if not expected or expected == 'change-me' or x_admin_bootstrap_token != expected:
        raise HTTPException(status_code=403, detail='Bootstrap disabled or token invalid')
    user.is_admin = True
    audit(db, user, 'admin.bootstrap', 'user', user.id)
    db.commit()

@router.get('/overview')
async def overview(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    active_users = int(db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0)
    jobs_queued = int(db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status == 'queued')) or 0)
    failed_answers = int(db.scalar(select(func.count()).select_from(AnswerAudit).where(AnswerAudit.status == 'failed')) or 0)
    open_risk_events = int(db.scalar(select(func.count()).select_from(RiskEvent).where(RiskEvent.state.in_(['new','reviewing']))) or 0)
    open_safety_cases = int(db.scalar(select(func.count()).select_from(SafetyCase).where(SafetyCase.status.in_(['open','reviewing','restricted']))) or 0)
    open_frustration_events = int(db.scalar(select(func.count()).select_from(FrustrationEvent).where(FrustrationEvent.resolved.is_(False))) or 0)
    usage = db.execute(select(func.coalesce(func.sum(UsageEvent.inference_ms),0), func.coalesce(func.avg(UsageEvent.queue_ms),0))).one()
    llama_ok = await request.app.state.llama.health()
    return {'users': users, 'active_users': active_users, 'jobs_queued': jobs_queued, 'failed_answer_audits': failed_answers,
            'open_risk_events': open_risk_events, 'open_safety_cases': open_safety_cases, 'open_frustration_events': open_frustration_events,
            'inference_ms_total': int(usage[0]), 'avg_queue_ms': round(float(usage[1]),2), 'inference_queue_waiting': request.app.state.governor.waiting,
            'local_inference': bool(llama_ok)}

@router.get('/frustration/summary')
def frustration_overview(hours:int=Query(default=24,ge=1,le=720), admin:User=Depends(require_admin), db:Session=Depends(get_db))->dict:
    return frustration_summary(db, hours)

@router.get('/frustration/events')
def frustration_events(kind:str|None=None, severity:str|None=None, resolved:bool|None=None, limit:int=Query(default=100,ge=1,le=500), admin:User=Depends(require_admin), db:Session=Depends(get_db))->list[dict]:
    stmt=select(FrustrationEvent).order_by(FrustrationEvent.created_at.desc()).limit(limit)
    if kind: stmt=stmt.where(FrustrationEvent.kind==kind)
    if severity: stmt=stmt.where(FrustrationEvent.severity==severity)
    if resolved is not None: stmt=stmt.where(FrustrationEvent.resolved.is_(resolved))
    rows=db.scalars(stmt).all()
    return [{'id':x.id,'user_id':x.user_id,'project_id':x.project_id,'conversation_id':x.conversation_id,'request_id':x.request_id,'kind':x.kind,'severity':x.severity,'source':x.source,'metrics':x.metrics,'resolved':x.resolved,'created_at':x.created_at} for x in rows]

@router.post('/frustration/events/{event_id}/resolve', status_code=204)
def resolve_frustration(event_id:str, admin:User=Depends(require_admin), db:Session=Depends(get_db))->None:
    row=db.get(FrustrationEvent,event_id)
    if row is None: raise HTTPException(status_code=404,detail='Frustration event not found')
    row.resolved=True
    audit(db,admin,'frustration.resolve','frustration_event',row.id,{'kind':row.kind})
    db.commit()

@router.get('/users')
def list_users(q: str = Query(default='', max_length=200), limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if q.strip(): stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
    rows = db.scalars(stmt).all()
    quotas = {x.user_id:x for x in db.scalars(select(UserQuota).where(UserQuota.user_id.in_([u.id for u in rows]))).all()} if rows else {}
    return [{'id':u.id,'email':u.email,'display_name':u.display_name,'is_active':u.is_active,'is_admin':u.is_admin,'created_at':u.created_at,
             'plan': quotas[u.id].plan if u.id in quotas else None} for u in rows]

@router.patch('/users/{user_id}')
def patch_user(user_id: str, payload: AdminUserPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    target = db.get(User, user_id)
    if target is None: raise HTTPException(status_code=404, detail='User not found')
    if target.id == admin.id and payload.is_active is False: raise HTTPException(status_code=409, detail='Administrator cannot deactivate own account here')
    changes={}
    if payload.is_active is not None and target.is_active != payload.is_active:
        target.is_active=payload.is_active; changes['is_active']=payload.is_active
        if not payload.is_active:
            now=datetime.now(timezone.utc)
            for sess in db.scalars(select(AuthSession).where(AuthSession.user_id==target.id, AuthSession.revoked_at.is_(None))).all(): sess.revoked_at=now
    quota=db.get(UserQuota,target.id)
    if quota is None:
        quota=UserQuota(user_id=target.id); db.add(quota)
    for name in ('plan','monthly_compute_seconds_limit','max_concurrent_inference','max_concurrent_jobs'):
        value=getattr(payload,name)
        if value is not None: setattr(quota,name,value); changes[name]=value
    audit(db, admin, 'user.update', 'user', target.id, changes); db.commit()
    return {'id':target.id,'changes':changes}

@router.post('/users/{user_id}/revoke-sessions', status_code=204)
def revoke_sessions(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    target=db.get(User,user_id)
    if target is None: raise HTTPException(status_code=404, detail='User not found')
    now=datetime.now(timezone.utc); count=0
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id==user_id, AuthSession.revoked_at.is_(None))).all(): sess.revoked_at=now; count+=1
    audit(db,admin,'user.revoke_sessions','user',user_id,{'count':count}); db.commit()

@router.get('/search-providers')
def search_providers(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [{'provider':s.provider,'request_count':s.request_count,'success_count':s.success_count,'failure_count':s.failure_count,
             'last_latency_ms':s.last_latency_ms,'last_error':s.last_error,'last_checked_at':s.last_checked_at} for s in db.scalars(select(SearchProviderStat)).all()]

@router.get('/answer-audits')
def answer_audits(limit:int=Query(default=50,ge=1,le=200), status_filter:str|None=Query(default=None,alias='status'), admin:User=Depends(require_admin), db:Session=Depends(get_db))->list[dict]:
    stmt=select(AnswerAudit).order_by(AnswerAudit.created_at.desc()).limit(limit)
    if status_filter: stmt=stmt.where(AnswerAudit.status==status_filter)
    return [{'id':a.id,'user_id':a.user_id,'request_id':a.request_id,'status':a.status,'warnings':a.warnings,'created_at':a.created_at} for a in db.scalars(stmt).all()]

@router.get('/settings')
def settings(admin:User=Depends(require_admin), db:Session=Depends(get_db))->dict:
    return {x.key:x.value for x in db.scalars(select(SystemSetting)).all()}

@router.put('/settings/{key}')
def put_setting(key:str,payload:SettingPatch,admin:User=Depends(require_admin),db:Session=Depends(get_db))->dict:
    if not key or len(key)>120: raise HTTPException(status_code=422,detail='Invalid setting key')
    row=db.get(SystemSetting,key)
    if row is None: row=SystemSetting(key=key,value=payload.value,updated_by=admin.id); db.add(row)
    else: row.value=payload.value; row.updated_by=admin.id
    audit(db,admin,'setting.update','setting',key,{'value':payload.value}); db.commit()
    return {'key':key,'value':payload.value}

@router.get('/audit-log')
def audit_log(limit:int=Query(default=100,ge=1,le=500),admin:User=Depends(require_admin),db:Session=Depends(get_db))->list[dict]:
    rows=db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)).all()
    return [{'id':r.id,'actor_user_id':r.actor_user_id,'action':r.action,'target_type':r.target_type,'target_id':r.target_id,'details':r.details,'created_at':r.created_at} for r in rows]


@router.post('/performance/snapshots')
def create_performance_snapshot(window_minutes:int=Query(default=60,ge=5,le=10080), admin:User=Depends(require_admin), db:Session=Depends(get_db))->dict:
    row=build_snapshot(db,window_minutes)
    audit(db,admin,'performance.snapshot','performance_snapshot',row.id,{'window_minutes':window_minutes})
    db.commit(); db.refresh(row)
    return {'id':row.id,'window_minutes':row.window_minutes,'request_count':row.request_count,'success_count':row.success_count,
            'p50_duration_ms':row.p50_duration_ms,'p95_duration_ms':row.p95_duration_ms,'p99_duration_ms':row.p99_duration_ms,
            'p50_queue_ms':row.p50_queue_ms,'p95_queue_ms':row.p95_queue_ms,'p95_inference_ms':row.p95_inference_ms,
            'cpu_seconds_per_success':row.cpu_seconds_per_success,'context_efficiency_ratio':row.context_efficiency_ratio,
            'frustration_count':row.frustration_count,'quality_failure_count':row.quality_failure_count,'metrics':row.metrics,'created_at':row.created_at}

@router.get('/performance/snapshots')
def list_performance_snapshots(limit:int=Query(default=50,ge=1,le=200), admin:User=Depends(require_admin), db:Session=Depends(get_db))->list[dict]:
    rows=db.scalars(select(PerformanceSnapshot).order_by(PerformanceSnapshot.created_at.desc()).limit(limit)).all()
    return [{'id':r.id,'window_minutes':r.window_minutes,'request_count':r.request_count,'success_count':r.success_count,
             'p95_duration_ms':r.p95_duration_ms,'p95_queue_ms':r.p95_queue_ms,'cpu_seconds_per_success':r.cpu_seconds_per_success,
             'context_efficiency_ratio':r.context_efficiency_ratio,'frustration_count':r.frustration_count,
             'quality_failure_count':r.quality_failure_count,'metrics':r.metrics,'created_at':r.created_at} for r in rows]

@router.post('/performance/experiments')
def create_optimization_experiment(payload:OptimizationExperimentCreate, admin:User=Depends(require_admin), db:Session=Depends(get_db))->dict:
    decision,reasons=evaluate_candidate(payload.baseline,payload.candidate)
    row=OptimizationExperiment(name=payload.name,baseline=payload.baseline,candidate=payload.candidate,decision=decision,reasons=reasons,created_by=admin.id)
    db.add(row); db.flush()
    audit(db,admin,'performance.experiment','optimization_experiment',row.id,{'decision':decision,'reasons':reasons})
    db.commit(); db.refresh(row)
    return {'id':row.id,'name':row.name,'decision':row.decision,'reasons':row.reasons,'created_at':row.created_at}

@router.get('/performance/experiments')
def list_optimization_experiments(limit:int=Query(default=50,ge=1,le=200), admin:User=Depends(require_admin), db:Session=Depends(get_db))->list[dict]:
    rows=db.scalars(select(OptimizationExperiment).order_by(OptimizationExperiment.created_at.desc()).limit(limit)).all()
    return [{'id':r.id,'name':r.name,'decision':r.decision,'reasons':r.reasons,'baseline':r.baseline,'candidate':r.candidate,'created_at':r.created_at} for r in rows]
