from __future__ import annotations
from datetime import datetime, timedelta, timezone
from math import ceil
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import BetaParticipant, BetaSnapshot, FrustrationEvent, Task, UsageEvent

def _now(): return datetime.now(timezone.utc)
def _aware(v): return v if v is None or v.tzinfo else v.replace(tzinfo=timezone.utc)
def _p95(values):
    if not values: return 0
    vals=sorted(int(x) for x in values); return vals[max(0,ceil(.95*len(vals))-1)]

def build_beta_snapshot(db:Session,*,cohort:str='closed-beta-1',window_days:int=30,now:datetime|None=None)->BetaSnapshot:
    now=_aware(now) or _now(); since=now-timedelta(days=max(1,window_days))
    participants=list(db.scalars(select(BetaParticipant).where(BetaParticipant.cohort==cohort,BetaParticipant.enrolled_at>=since)).all()); ids=[x.user_id for x in participants]
    usage=list(db.scalars(select(UsageEvent).where(UsageEvent.user_id.in_(ids),UsageEvent.created_at>=since)).all()) if ids else []
    tasks=list(db.scalars(select(Task).where(Task.created_by.in_(ids),Task.created_at>=since)).all()) if ids else []
    frustration=list(db.scalars(select(FrustrationEvent).where(FrustrationEvent.user_id.in_(ids),FrustrationEvent.created_at>=since)).all()) if ids else []
    by_user={uid:[] for uid in ids}
    for event in usage: by_user.setdefault(event.user_id,[]).append(_aware(event.created_at))
    activated=sum(bool(by_user.get(p.user_id)) for p in participants); d1e=d1r=d7e=d7r=0
    for p in participants:
        enrolled=_aware(p.enrolled_at); events=by_user.get(p.user_id,[])
        if now>=enrolled+timedelta(days=1): d1e+=1; d1r+=int(any(enrolled+timedelta(days=1)<=x<enrolled+timedelta(days=2) for x in events))
        if now>=enrolled+timedelta(days=7): d7e+=1; d7r+=int(any(enrolled+timedelta(days=7)<=x<enrolled+timedelta(days=8) for x in events))
    success=sum(bool(x.success) for x in usage); compute_ms=sum(max(0,int(x.inference_ms or 0)) for x in usage); completed=sum(x.status=='completed' for x in tasks); cpu_min=compute_ms/60000.0
    metrics={'d1_retention':0 if not d1e else round(d1r/d1e,4),'d7_retention':0 if not d7e else round(d7r/d7e,4),'request_success_rate':0 if not usage else round(success/len(usage),4),'task_completion_rate':0 if not tasks else round(completed/len(tasks),4),'frustration_per_request':0 if not usage else round(len(frustration)/len(usage),4),'verified_task_success_per_cpu_minute':0 if cpu_min<=0 else round(completed/cpu_min,6)}
    readiness={'participant_target_met':50<=len(participants)<=100,'task_target_met':len(tasks)>=500,'capacity_recalibration_ready':50<=len(participants)<=100 and len(tasks)>=500}
    row=BetaSnapshot(cohort=cohort,window_days=window_days,enrolled_count=len(participants),activated_count=activated,d1_eligible_count=d1e,d1_retained_count=d1r,d7_eligible_count=d7e,d7_retained_count=d7r,request_count=len(usage),success_count=success,task_count=len(tasks),completed_task_count=completed,frustration_count=len(frustration),compute_minutes_total=round(cpu_min,4),compute_minutes_per_active_user=0 if not activated else round(cpu_min/activated,4),p95_duration_ms=_p95([x.duration_ms for x in usage]),p95_queue_ms=_p95([x.queue_ms for x in usage]),metrics=metrics,readiness=readiness); db.add(row); db.flush(); return row

def snapshot_dict(x:BetaSnapshot)->dict:
    return {k:getattr(x,k) for k in ('id','cohort','window_days','enrolled_count','activated_count','d1_eligible_count','d1_retained_count','d7_eligible_count','d7_retained_count','request_count','success_count','task_count','completed_task_count','frustration_count','compute_minutes_total','compute_minutes_per_active_user','p95_duration_ms','p95_queue_ms','metrics','readiness','created_at')}
