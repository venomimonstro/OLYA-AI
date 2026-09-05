from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.models import AnswerAudit, BackgroundJob, EngineeringExecution, FrustrationEvent, ImageGeneration, UsageEvent
from app.models_sprint31 import SystemCheckpoint, SystemHealthSnapshot


def _now() -> datetime: return datetime.now(timezone.utc)
def _count(db: Session, stmt) -> int: return int(db.scalar(stmt) or 0)

def _checkpoint(key:str, subsystem:str, status:str, message:str, *, critical:bool=False, dependency:str="", latency_ms:int=0, details:dict[str,Any]|None=None)->dict[str,Any]:
    return {"key":key,"subsystem":subsystem,"status":status,"message":message,"critical":critical,"dependency":dependency,"latency_ms":latency_ms,"severity":"critical" if critical and status=="failed" else "warning" if status!="stable" else "info","details":details or {}}

def _persist(db:Session,item:dict[str,Any])->None:
    row=db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key==item["key"])); now=_now()
    if row is None:
        row=SystemCheckpoint(key=item["key"],subsystem=item["subsystem"]); db.add(row); db.flush()
    prev=row.consecutive_failures; row.subsystem=item["subsystem"]; row.dependency=item.get("dependency",""); row.status=item["status"]; row.severity=item["severity"]; row.critical=bool(item.get("critical")); row.latency_ms=int(item.get("latency_ms",0)); row.message=str(item["message"])[:500]; row.details=item.get("details",{}); row.last_checked_at=now
    if item["status"]=="stable": row.consecutive_failures=0; row.last_ok_at=now
    else: row.consecutive_failures=prev+1

def _aggregate(checks:list[dict[str,Any]])->dict[str,Any]:
    stable=sum(x["status"]=="stable" for x in checks); degraded=sum(x["status"]=="degraded" for x in checks); failed=sum(x["status"]=="failed" for x in checks); critical_failed=sum(x["status"]=="failed" and x.get("critical") for x in checks)
    status="failed" if critical_failed else "degraded" if failed or degraded else "stable"; score=max(0,min(100,round((stable*100+degraded*55)/max(1,len(checks)))))
    return {"status":status,"score":score,"stable":stable,"degraded":degraded,"failed":failed,"critical_failed":critical_failed,"checks":checks,"checked_at":_now()}

async def collect_system_health(app,db:Session,*,persist:bool=True)->dict[str,Any]:
    checks=[]; started=perf_counter()
    try:
        db.execute(text("SELECT 1")); db_ms=int((perf_counter()-started)*1000); checks.append(_checkpoint("core.database","core","stable","Database query succeeded",critical=True,latency_ms=db_ms))
    except Exception as exc:
        db.rollback(); checks.append(_checkpoint("core.database","core","failed",f"Database unavailable: {type(exc).__name__}",critical=True)); return _aggregate(checks)
    required={"users","conversations","messages","usage_events","background_jobs","image_generations","engineering_runs"}; tables=set(inspect(db.get_bind()).get_table_names()); missing=sorted(required-tables)
    checks.append(_checkpoint("core.schema","core","failed" if missing else "stable","Critical schema tables are missing" if missing else "Critical schema is present",critical=True,dependency="core.database",details={"missing":missing}))
    llama=getattr(app.state,"llama",None); started=perf_counter()
    if llama is None: inference_ok=False; error="client_not_initialized"
    else:
        try: inference_ok=bool(await llama.health()); error=""
        except Exception as exc: inference_ok=False; error=type(exc).__name__
    checks.append(_checkpoint("core.inference","inference","stable" if inference_ok else "failed","Local inference healthy" if inference_ok else "Local inference unavailable",critical=True,latency_ms=int((perf_counter()-started)*1000),details={"error":error}))
    governor=getattr(app.state,"governor",None); waiting=int(getattr(governor,"waiting",0)) if governor else 0; max_queue=max(1,int(getattr(governor,"max_queue",1))) if governor else 1
    stale=_count(db,select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status=="running",BackgroundJob.lease_expires_at.is_not(None),BackgroundJob.lease_expires_at<_now())); ratio=waiting/max_queue; qstatus="failed" if stale or ratio>=1 else "degraded" if ratio>=.7 else "stable"
    checks.append(_checkpoint("runtime.queue","runtime",qstatus,"Queue pressure or stale leases detected" if qstatus!="stable" else "Queue healthy",critical=True,details={"waiting":waiting,"max_queue":max_queue,"stale_job_leases":stale}))
    since=_now()-timedelta(hours=1); req=_count(db,select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at>=since)); bad=_count(db,select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at>=since,UsageEvent.success.is_(False))); ratio=bad/req if req else 0.0; status="failed" if req>=5 and ratio>=.20 else "degraded" if req>=5 and ratio>=.05 else "stable"
    checks.append(_checkpoint("link.chat_pipeline","chat",status,"Chat pipeline failure rate elevated" if status!="stable" else "Chat pipeline healthy",critical=True,details={"requests_1h":req,"failed_1h":bad,"failure_rate":round(ratio,4)}))
    quality=_count(db,select(func.count()).select_from(AnswerAudit).where(AnswerAudit.created_at>=since)); quality_bad=_count(db,select(func.count()).select_from(AnswerAudit).where(AnswerAudit.created_at>=since,AnswerAudit.status=="failed")); qratio=quality_bad/quality if quality else 0.0
    checks.append(_checkpoint("link.quality","quality","degraded" if quality>=5 and qratio>=.10 else "stable","Quality failure rate elevated" if quality>=5 and qratio>=.10 else "Answer verification healthy",details={"audits_1h":quality,"failed_1h":quality_bad,"failure_rate":round(qratio,4)}))
    itotal=_count(db,select(func.count()).select_from(ImageGeneration).where(ImageGeneration.created_at>=since)); ifailed=_count(db,select(func.count()).select_from(ImageGeneration).where(ImageGeneration.created_at>=since,ImageGeneration.status.in_(["failed","error"]))); iold=_count(db,select(func.count()).select_from(ImageGeneration).where(ImageGeneration.status=="queued",ImageGeneration.created_at<_now()-timedelta(minutes=10))); iratio=ifailed/itotal if itotal else 0.0; istatus="failed" if iold>=3 else "degraded" if (itotal>=3 and iratio>=.15) or iold else "stable"
    checks.append(_checkpoint("link.image_pipeline","images",istatus,"Image generation instability detected" if istatus!="stable" else "Image generation healthy",details={"generations_1h":itotal,"failed_1h":ifailed,"stale_queue":iold,"failure_rate":round(iratio,4)}))
    atotal=_count(db,select(func.count()).select_from(EngineeringExecution).where(EngineeringExecution.created_at>=_now()-timedelta(hours=24))); afailed=_count(db,select(func.count()).select_from(EngineeringExecution).where(EngineeringExecution.created_at>=_now()-timedelta(hours=24),EngineeringExecution.status.in_(["blocked","rolled_back"]))); aratio=afailed/atotal if atotal else 0.0; astatus="degraded" if atotal>=3 and aratio>=.20 else "stable"
    checks.append(_checkpoint("link.agent_pipeline","engineering",astatus,"Agent execution failure rate elevated" if astatus!="stable" else "Agent execution pipeline healthy",details={"executions_24h":atotal,"blocked_or_rolled_back":afailed,"failure_rate":round(aratio,4)}))
    fr=_count(db,select(func.count()).select_from(FrustrationEvent).where(FrustrationEvent.created_at>=since,FrustrationEvent.resolved.is_(False))); checks.append(_checkpoint("ux.frustration","ux","degraded" if fr>=5 else "stable","User frustration signals elevated" if fr>=5 else "User frustration signals normal",details={"open_events_1h":fr}))
    result=_aggregate(checks)
    if persist:
        for item in checks: _persist(db,item)
        snap=SystemHealthSnapshot(overall_status=result["status"],score=result["score"],stable_count=result["stable"],degraded_count=result["degraded"],failed_count=result["failed"],critical_failed_count=result["critical_failed"],checks=checks); db.add(snap); db.flush(); result["snapshot_id"]=snap.id
    return result

def latest_checkpoints(db:Session)->list[dict[str,Any]]:
    rows=list(db.scalars(select(SystemCheckpoint).order_by(SystemCheckpoint.critical.desc(),SystemCheckpoint.subsystem,SystemCheckpoint.key)).all())
    return [{"key":x.key,"subsystem":x.subsystem,"dependency":x.dependency,"status":x.status,"severity":x.severity,"critical":x.critical,"latency_ms":x.latency_ms,"message":x.message,"details":x.details,"consecutive_failures":x.consecutive_failures,"last_ok_at":x.last_ok_at,"last_checked_at":x.last_checked_at} for x in rows]
