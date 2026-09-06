from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnswerAudit,
    BetaParticipant,
    BetaSnapshot,
    CapacityPlan,
    CircuitBreakerEvent,
    FrustrationEvent,
    ImageGeneration,
    MeasuredPlanCatalog,
    ProjectSandboxRun,
    PublicRollout,
    ResourceExpenseEvent,
    SystemCheckpoint,
    UsageEvent,
    UserQuota,
)
from app.services.beta_trends import build_trend
from app.services.capacity import read_capacity_report

ROLLOUT_STAGES = (1, 5, 10, 25, 50, 100)
PLAN_ORDER = ("free", "x1", "pro", "max", "business")
LAUNCH_EVIDENCE_KEYS = ("ops.release_gate", "ops.restore_drill", "ops.backup", "quality.release_gate")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _elapsed_ms(start, end) -> int:
    start, end = _aware(start), _aware(end)
    return 0 if not start or not end or end <= start else int((end - start).total_seconds() * 1000)


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    values = sorted(max(0, int(v or 0)) for v in values)
    return values[max(0, min(len(values) - 1, ceil(q * len(values)) - 1))]


def active_rollout(db: Session) -> PublicRollout | None:
    return db.scalar(select(PublicRollout).where(PublicRollout.state == "active").order_by(PublicRollout.version.desc()).limit(1))


def active_measured_catalog(db: Session) -> MeasuredPlanCatalog | None:
    return db.scalar(select(MeasuredPlanCatalog).where(MeasuredPlanCatalog.status == "active").order_by(MeasuredPlanCatalog.version.desc()).limit(1))


def rollout_dict(row: PublicRollout) -> dict[str, Any]:
    return {"id":row.id,"version":row.version,"state":row.state,"exposure_percent":row.exposure_percent,"baseline_metrics":row.baseline_metrics,"latest_metrics":row.latest_metrics,"guardrails":row.guardrails,"decision":row.decision,"previous_rollout_id":row.previous_rollout_id,"opened_at":row.opened_at,"paused_at":row.paused_at,"completed_at":row.completed_at,"rolled_back_at":row.rolled_back_at,"created_at":row.created_at,"updated_at":row.updated_at}


def catalog_dict(row: MeasuredPlanCatalog) -> dict[str, Any]:
    return {"id":row.id,"version":row.version,"status":row.status,"source":row.source,"catalog":row.catalog,"economics":row.economics,"guardrails":row.guardrails,"previous_catalog_id":row.previous_catalog_id,"approved_at":row.approved_at,"activated_at":row.activated_at,"created_at":row.created_at,"updated_at":row.updated_at}


def assignment_bucket(user_id: str, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 10_000


def rollout_allows_user(row: PublicRollout | None, user_id: str) -> bool:
    if row is None or row.state != "active" or row.exposure_percent <= 0:
        return False
    return True if row.exposure_percent >= 100 else assignment_bucket(user_id, row.assignment_salt) < row.exposure_percent * 100


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _normalized_channel_shares(settings) -> dict[str, float]:
    raw={"fast":float(getattr(settings,"plan_share_fast",.20)),"work":float(getattr(settings,"plan_share_work",.35)),"deep":float(getattr(settings,"plan_share_deep",.25)),"api":float(getattr(settings,"plan_share_api",.10)),"image_worker":float(getattr(settings,"plan_share_image",.05)),"sandbox":float(getattr(settings,"plan_share_sandbox",.05))}
    total=sum(max(0.0,v) for v in raw.values()) or 1.0
    return {k:round(max(0.0,v)/total,6) for k,v in raw.items()}


def build_measured_plan_catalog(beta_metrics: dict[str, Any], capacity_report: dict[str, Any], settings) -> dict[str, Any]:
    readiness=dict(beta_metrics.get("readiness") or {}); blockers=[]
    if not readiness.get("capacity_recalibration_ready"): blockers.append("beta_sample_not_sufficient")
    if capacity_report.get("status")!="passed": blockers.append("target_node_capacity_not_current")
    measured_minutes=float(beta_metrics.get("compute_minutes_per_active_user") or 0.0)
    if measured_minutes<=0: blockers.append("measured_compute_per_user_missing")
    base_seconds=ceil(measured_minutes*60.0*max(1.0,float(getattr(settings,"capacity_compute_headroom_ratio",1.25))))
    base_seconds=_clamp(base_seconds or int(getattr(settings,"default_monthly_compute_seconds",600)),int(getattr(settings,"capacity_min_monthly_compute_seconds",300)),int(getattr(settings,"capacity_max_monthly_compute_seconds",14400)))
    ratios={"free":float(getattr(settings,"plan_ratio_free",.25)),"x1":float(getattr(settings,"plan_ratio_x1",1.0)),"pro":float(getattr(settings,"plan_ratio_pro",2.0)),"max":float(getattr(settings,"plan_ratio_max",4.0)),"business":float(getattr(settings,"plan_ratio_business",8.0))}
    rec=dict(capacity_report.get("recommendation") or {}); node_concurrency=max(1,int(rec.get("max_concurrent_generations") or 1)); cpu_rate=max(1,int(getattr(settings,"commerce_cpu_microunits_per_second",1000))); shares=_normalized_channel_shares(settings)
    catalog={}
    for name in PLAN_ORDER:
        seconds=_clamp(ceil(base_seconds*max(.05,ratios[name])),int(getattr(settings,"capacity_min_monthly_compute_seconds",300)),int(getattr(settings,"capacity_max_monthly_compute_seconds",14400))*(4 if name=="business" else 1))
        catalog[name]={"name":name,"monthly_cpu_seconds":seconds,"resource_budget_microunits":seconds*cpu_rate,"max_concurrent_inference":node_concurrency,"max_concurrent_jobs":max(1,min(16,int(getattr(settings,"default_max_concurrent_jobs",1))*(1 if name=="free" else 2 if name in {"x1","pro"} else 4))),"organization_enabled":name=="business","channel_shares":shares,"source":"measured_beta_cpu_with_operator_plan_ratios"}
    metrics=dict(beta_metrics.get("metrics") or {})
    return {"status":"ready" if not blockers else "blocked","blockers":blockers,"catalog":catalog,"economics":{"measured_compute_minutes_per_active_user":measured_minutes,"base_monthly_compute_seconds_with_headroom":base_seconds,"monthly_server_cost_rub":float(getattr(settings,"monthly_server_cost_rub",0.0)),"verified_request_success_per_cpu_minute":metrics.get("verified_request_success_per_cpu_minute",0),"completed_task_success_per_cpu_minute":metrics.get("completed_task_success_per_cpu_minute",metrics.get("verified_task_success_per_cpu_minute",0)),"node_max_concurrent_generations":node_concurrency},"policy":{"plan_ratios":ratios,"channel_shares":shares,"note":"Ratios are operator product policy; base capacity is measured from beta telemetry."}}


def create_measured_catalog(db: Session, *, measured: dict[str, Any], actor_id: str | None) -> MeasuredPlanCatalog:
    if measured.get("status")!="ready" or measured.get("blockers"): raise ValueError("Measured plan catalog cannot be created before beta/capacity gates pass")
    current=active_measured_catalog(db); version=int(db.scalar(select(func.max(MeasuredPlanCatalog.version))) or 0)+1
    row=MeasuredPlanCatalog(version=version,status="draft",catalog=dict(measured.get("catalog") or {}),economics=dict(measured.get("economics") or {}),guardrails={"blockers":[],"policy":measured.get("policy") or {}},previous_catalog_id=current.id if current else None,created_by=actor_id); db.add(row); db.flush(); return row


def approve_catalog(row: MeasuredPlanCatalog, actor_id: str | None) -> None:
    if row.status!="draft": raise ValueError("Only a draft measured catalog can be approved")
    if not row.catalog or (row.guardrails or {}).get("blockers"): raise ValueError("Measured catalog has unresolved blockers")
    row.status="approved"; row.approved_by=actor_id; row.approved_at=utcnow()


def activate_catalog(db: Session, row: MeasuredPlanCatalog) -> None:
    if row.status!="approved": raise ValueError("Measured catalog must be approved before activation")
    current=active_measured_catalog(db)
    if current is not None and current.id!=row.id:
        current.status="superseded"; row.previous_catalog_id=row.previous_catalog_id or current.id
    row.status="active"; row.activated_at=utcnow()


def rollback_catalog(db: Session, current: MeasuredPlanCatalog, *, actor_id: str | None) -> MeasuredPlanCatalog:
    if current.status!="active": raise ValueError("Only the active measured catalog can be rolled back")
    if not current.previous_catalog_id: raise ValueError("Active measured catalog has no previous catalog")
    target=db.get(MeasuredPlanCatalog,current.previous_catalog_id)
    if target is None or target.status not in {"superseded","active"}: raise ValueError("Previous measured catalog is unavailable")
    version=int(db.scalar(select(func.max(MeasuredPlanCatalog.version))) or 0)+1; current.status="superseded"
    row=MeasuredPlanCatalog(version=version,status="active",source="rollback",catalog=dict(target.catalog or {}),economics=dict(target.economics or {}),guardrails={"blockers":[],"rollback_of_version":target.version},previous_catalog_id=current.id,created_by=actor_id,approved_by=actor_id,approved_at=utcnow(),activated_at=utcnow()); db.add(row); db.flush(); return row


def open_breakers(db: Session, scope: str="public-launch") -> list[CircuitBreakerEvent]:
    return list(db.scalars(select(CircuitBreakerEvent).where(CircuitBreakerEvent.scope==scope,CircuitBreakerEvent.status=="open")).all())


def user_has_open_breaker(db: Session, user_id: str) -> bool:
    return db.scalar(select(CircuitBreakerEvent.id).where(CircuitBreakerEvent.scope==f"user:{user_id}",CircuitBreakerEvent.status=="open").limit(1)) is not None


def trip_breaker(db: Session, *, kind: str, reason: str, details: dict[str, Any]|None=None, scope: str="public-launch", automatic: bool=True, actor_id: str|None=None) -> CircuitBreakerEvent:
    existing=db.scalar(select(CircuitBreakerEvent).where(CircuitBreakerEvent.scope==scope,CircuitBreakerEvent.kind==kind,CircuitBreakerEvent.status=="open"))
    if existing is not None: existing.reason=reason[:500]; existing.details=details or {}; return existing
    row=CircuitBreakerEvent(scope=scope,kind=kind,status="open",automatic=automatic,reason=reason[:500],details=details or {},opened_by=actor_id); db.add(row); db.flush(); return row


def resolve_breaker(row: CircuitBreakerEvent, actor_id: str|None) -> None:
    if row.status!="open": return
    row.status="resolved"; row.resolved_by=actor_id; row.resolved_at=utcnow()


def _latest_trend(db: Session, settings, cohort: str="closed-beta-1") -> dict[str,Any]:
    rows=list(db.scalars(select(BetaSnapshot).where(BetaSnapshot.cohort==cohort).order_by(BetaSnapshot.created_at.desc()).limit(14)).all())
    if len(rows)<2:return {"status":"insufficient_data","latest":None}
    window=rows[0].window_days; rows=[r for r in rows if r.window_days==window]
    return build_trend(rows,settings) if len(rows)>=2 else {"status":"insufficient_data","latest":None}


def _launch_evidence(db: Session, settings, now: datetime) -> dict[str,Any]:
    rows=list(db.scalars(select(SystemCheckpoint).where(SystemCheckpoint.key.in_(LAUNCH_EVIDENCE_KEYS))).all()); by_key={r.key:r for r in rows}; stale=max(60,int(getattr(settings,"health_checkpoint_stale_seconds",300))); problems=[]; details={}
    for key in LAUNCH_EVIDENCE_KEYS:
        row=by_key.get(key)
        if row is None: problems.append(f"{key}:missing"); details[key]={"status":"missing"}; continue
        checked=_aware(row.last_checked_at); age=None if checked is None else max(0.0,(now-checked).total_seconds()); status=row.status
        if status!="stable": problems.append(f"{key}:{status}")
        elif age is None or age>stale: problems.append(f"{key}:stale")
        details[key]={"status":status,"age_seconds":None if age is None else round(age,1)}
    return {"status":"stable" if not problems else "blocked","problems":problems,"details":details,"stale_after_seconds":stale}


def _global_resource_spend(db: Session, settings, since: datetime) -> dict[str,Any]:
    from app.services.commerce import price_resource_ms
    cpu_ms=int(db.scalar(select(func.coalesce(func.sum(UsageEvent.inference_ms),0)).where(UsageEvent.created_at>=since)) or 0)
    gpu_ms=int(db.scalar(select(func.coalesce(func.sum(ResourceExpenseEvent.quantity_ms),0)).where(ResourceExpenseEvent.created_at>=since,ResourceExpenseEvent.resource_kind=="gpu")) or 0)
    images=list(db.scalars(select(ImageGeneration).where(ImageGeneration.created_at>=since,ImageGeneration.started_at.is_not(None),ImageGeneration.finished_at.is_not(None))).all()); image_ms=sum(_elapsed_ms(x.started_at,x.finished_at) for x in images)
    sandboxes=list(db.scalars(select(ProjectSandboxRun).where(ProjectSandboxRun.created_at>=since,ProjectSandboxRun.started_at.is_not(None),ProjectSandboxRun.completed_at.is_not(None))).all()); sandbox_ms=sum(_elapsed_ms(x.started_at,x.completed_at) for x in sandboxes)
    usage={"cpu":cpu_ms,"gpu":gpu_ms,"image_worker":image_ms,"sandbox":sandbox_ms}; costs={k:price_resource_ms(settings,k,v) for k,v in usage.items()}
    return {"usage_ms":usage,"cost_microunits":costs,"total_cost_microunits":sum(costs.values())}


def _derived_global_budget(db: Session, catalog: MeasuredPlanCatalog|None) -> int:
    if catalog is None or not catalog.catalog:return 0
    budget=0
    for plan_name,count in db.execute(select(UserQuota.plan,func.count()).group_by(UserQuota.plan)).all():
        policy=(catalog.catalog or {}).get(plan_name)
        if isinstance(policy,dict):budget+=int(count or 0)*max(0,int(policy.get("resource_budget_microunits") or 0))
    return budget


def _trip_user_abuse_breakers(db: Session, settings, now: datetime, *, persist: bool) -> list[dict[str,Any]]:
    threshold=max(1,int(getattr(settings,"public_launch_max_requests_per_user_hour",120))); since=now-timedelta(hours=1); rows=db.execute(select(UsageEvent.user_id,func.count()).where(UsageEvent.created_at>=since).group_by(UsageEvent.user_id).having(func.count()>threshold)).all(); opened=[]
    for user_id,count in rows:
        info={"user_id":user_id,"requests_last_hour":int(count),"threshold":threshold}; opened.append(info)
        if persist: trip_breaker(db,kind="request_burst",reason="Per-user expensive request burst exceeded guardrail",details=info,scope=f"user:{user_id}")
    return opened


def _canary_metrics(db: Session, rollout: PublicRollout, now: datetime) -> dict[str,Any]:
    since=max(_aware(rollout.opened_at) or now-timedelta(hours=24),now-timedelta(hours=24)); beta_ids=set(db.scalars(select(BetaParticipant.user_id).where(BetaParticipant.state!="removed")).all()); events=list(db.scalars(select(UsageEvent).where(UsageEvent.created_at>=since,UsageEvent.created_at<=now)).all()); events=[e for e in events if e.user_id not in beta_ids and rollout_allows_user(rollout,e.user_id)]; request_ids={e.request_id for e in events if e.request_id}; success=sum(bool(e.success) for e in events); cpu_ms=sum(max(0,int(e.inference_ms or 0)) for e in events); cpu_minutes=cpu_ms/60000.0
    quality=list(db.scalars(select(AnswerAudit).where(AnswerAudit.request_id.in_(request_ids))).all()) if request_ids else []; qmap={q.request_id:q.status for q in quality}; supported=sum(qmap.get(r)=="supported" for r in request_ids)
    frustration=int(db.scalar(select(func.count(FrustrationEvent.id)).where(FrustrationEvent.request_id.in_(request_ids))) or 0) if request_ids else 0
    count=len(events)
    return {"request_count":count,"success_count":success,"request_success_rate":0 if not count else round(success/count,4),"frustration_count":frustration,"frustration_per_request":0 if not count else round(frustration/count,4),"p95_queue_ms":_percentile([e.queue_ms for e in events],.95),"p95_duration_ms":_percentile([e.duration_ms for e in events],.95),"quality_supported_request_rate":0 if not request_ids else round(supported/len(request_ids),4),"verified_request_success_per_cpu_minute":0 if cpu_minutes<=0 else round(supported/cpu_minutes,6),"cpu_minutes":round(cpu_minutes,4),"since":since.isoformat(),"measured_at":now.isoformat()}


def compare_canary_to_baseline(canary: dict[str,Any], baseline: dict[str,Any], settings) -> dict[str,Any]:
    bm=dict(baseline.get("metrics") or {}); min_requests=max(1,int(getattr(settings,"public_launch_canary_min_requests",30))); ready=int(canary.get("request_count") or 0)>=min_requests; regressions=[]
    if ready:
        if float(canary.get("request_success_rate") or 0)<float(bm.get("request_success_rate") or 0)-float(getattr(settings,"beta_trend_max_success_drop",.02)):regressions.append("canary_success_rate_regressed")
        if float(canary.get("frustration_per_request") or 0)>float(bm.get("frustration_per_request") or 0)+float(getattr(settings,"beta_trend_max_frustration_increase",.02)):regressions.append("canary_frustration_regressed")
        base_queue=int(baseline.get("p95_queue_ms") or 0); base_duration=int(baseline.get("p95_duration_ms") or 0)
        if base_queue and int(canary.get("p95_queue_ms") or 0)>base_queue*float(getattr(settings,"beta_wave_max_queue_regression_ratio",1.5)):regressions.append("canary_queue_regressed")
        if base_duration and int(canary.get("p95_duration_ms") or 0)>base_duration*float(getattr(settings,"beta_wave_max_duration_regression_ratio",1.5)):regressions.append("canary_latency_regressed")
        base_quality=float(bm.get("quality_supported_request_rate") or 0); current_quality=float(canary.get("quality_supported_request_rate") or 0)
        if base_quality and current_quality<base_quality-float(getattr(settings,"beta_trend_max_quality_drop",.05)):regressions.append("canary_quality_regressed")
        base_eff=float(bm.get("verified_request_success_per_cpu_minute") or 0); cur_eff=float(canary.get("verified_request_success_per_cpu_minute") or 0)
        if base_eff and cur_eff and cur_eff<base_eff*float(getattr(settings,"beta_wave_min_cpu_efficiency_ratio",.70)):regressions.append("canary_cpu_efficiency_regressed")
    return {"observation_ready":ready,"minimum_requests":min_requests,"regressions":regressions,"metrics":canary,"baseline":baseline}


def evaluate_public_launch(db: Session, settings, *, persist: bool=True) -> dict[str,Any]:
    blockers=[]; warnings=[]; now=utcnow(); capacity=read_capacity_report(settings)
    if capacity.get("status")!="passed":blockers.append("capacity_calibration_not_current")
    capacity_plan=db.scalar(select(CapacityPlan).where(CapacityPlan.status=="active").order_by(CapacityPlan.version.desc()).limit(1))
    if capacity_plan is None or not bool(capacity_plan.runtime_applied):blockers.append("active_capacity_plan_not_applied")
    catalog=active_measured_catalog(db)
    if catalog is None:blockers.append("measured_plan_catalog_missing")
    evidence=_launch_evidence(db,settings,now)
    if evidence["status"]!="stable":blockers.append("launch_evidence_not_current")
    critical=list(db.scalars(select(SystemCheckpoint).where(SystemCheckpoint.critical.is_(True),SystemCheckpoint.status!="stable")).all())
    if critical:blockers.append("critical_system_checkpoint_failed")
    trend=_latest_trend(db,settings)
    if trend.get("status")=="regressed":blockers.append("beta_trend_regressed")
    elif trend.get("status")=="insufficient_data":warnings.append("beta_trend_insufficient_data")
    rollout=active_rollout(db); canary={"observation_ready":False,"minimum_requests":int(getattr(settings,"public_launch_canary_min_requests",30)),"regressions":[],"metrics":{},"baseline":{}}
    if rollout is not None and rollout.exposure_percent>0:
        canary=compare_canary_to_baseline(_canary_metrics(db,rollout,now),dict(rollout.baseline_metrics or {}),settings)
        if canary["regressions"]:
            blockers.append("public_canary_regressed")
            if persist:trip_breaker(db,kind="canary_regression",reason="Public canary metrics regressed against last-known-good baseline",details=canary)
        elif not canary["observation_ready"]:warnings.append("canary_observation_incomplete")
    since=now-timedelta(hours=24); request_count=int(db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.created_at>=since)) or 0); failed_count=int(db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.created_at>=since,UsageEvent.success.is_(False))) or 0); failure_rate=failed_count/request_count if request_count else 0.0
    if request_count>=max(1,int(getattr(settings,"public_launch_breaker_min_requests",50))) and failure_rate>float(getattr(settings,"public_launch_max_failure_rate",.03)):
        blockers.append("global_failure_rate_breaker")
        if persist:trip_breaker(db,kind="failure_rate",reason="24h request failure rate exceeded public-launch guardrail",details={"requests":request_count,"failures":failed_count,"failure_rate":round(failure_rate,6)})
    user_abuse=_trip_user_abuse_breakers(db,settings,now,persist=persist)
    if user_abuse:warnings.append("per_user_abuse_breakers_opened")
    month_start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0); resource_spend=_global_resource_spend(db,settings,month_start); explicit=max(0,int(getattr(settings,"public_launch_global_budget_microunits",0))); budget=explicit or _derived_global_budget(db,catalog); budget_source="operator" if explicit else "active_measured_catalog"; spend=int(resource_spend["total_cost_microunits"])
    if budget>0 and spend>=budget:
        blockers.append("global_resource_budget_breaker")
        if persist:trip_breaker(db,kind="resource_budget",reason="Global measured resource budget exhausted",details={"spent_microunits":spend,"budget_microunits":budget,"budget_source":budget_source,**resource_spend})
    elif budget<=0:warnings.append("global_resource_budget_not_derivable")
    breaker_rows=open_breakers(db)
    if breaker_rows:blockers.append("open_circuit_breaker")
    return {"status":"stable" if not blockers else "blocked","blockers":sorted(set(blockers)),"warnings":sorted(set(warnings)),"capacity":capacity,"active_capacity_plan_id":capacity_plan.id if capacity_plan else None,"active_measured_catalog_id":catalog.id if catalog else None,"launch_evidence":evidence,"trend":trend,"canary":canary,"request_24h":{"count":request_count,"failed":failed_count,"failure_rate":round(failure_rate,6)},"user_abuse_breakers":user_abuse,"resource_month":{**resource_spend,"budget_microunits":budget,"budget_source":budget_source},"open_breakers":[{"id":x.id,"kind":x.kind,"reason":x.reason,"opened_at":x.opened_at} for x in breaker_rows],"evaluated_at":now.isoformat()}


def create_rollout(db: Session, *, baseline: dict[str,Any], actor_id: str|None) -> PublicRollout:
    existing=db.scalar(select(PublicRollout).where(PublicRollout.state.in_(["planned","active","paused"])).order_by(PublicRollout.version.desc()).limit(1))
    if existing is not None:raise ValueError(f"Public rollout v{existing.version} is still {existing.state}")
    version=int(db.scalar(select(func.max(PublicRollout.version))) or 0)+1; row=PublicRollout(version=version,state="planned",exposure_percent=0,baseline_metrics=baseline,latest_metrics=baseline,guardrails={"stages":list(ROLLOUT_STAGES)},decision={"status":"planned"},created_by=actor_id); db.add(row); db.flush(); return row


def _next_rollout_version(db: Session)->int:return int(db.scalar(select(func.max(PublicRollout.version))) or 0)+1


def _expected_next_stage(current_percent:int)->int|None:
    return next((stage for stage in ROLLOUT_STAGES if stage>current_percent),None)


def advance_rollout(db: Session,current:PublicRollout,*,exposure_percent:int,evaluation:dict[str,Any],actor_id:str|None)->PublicRollout:
    expected=_expected_next_stage(int(current.exposure_percent or 0))
    if exposure_percent not in ROLLOUT_STAGES:raise ValueError(f"Exposure must be one of {ROLLOUT_STAGES}")
    if expected is None or exposure_percent!=expected:raise ValueError(f"Next rollout stage must be {expected}%")
    if current.state not in {"planned","active","paused"}:raise ValueError("Rollout is not advanceable")
    if evaluation.get("status")!="stable":current.state="paused";current.paused_at=utcnow();current.decision={"status":"paused","evaluation":evaluation};raise ValueError("Public rollout guardrails are not green")
    canary=dict(evaluation.get("canary") or {})
    if int(current.exposure_percent or 0)>0 and not canary.get("observation_ready"):raise ValueError("Canary observation window is incomplete")
    baseline=dict(canary.get("metrics") or {}) if int(current.exposure_percent or 0)>0 else dict(current.baseline_metrics or {})
    if int(current.exposure_percent or 0)>0:
        baseline={"request_count":baseline.get("request_count",0),"success_count":baseline.get("success_count",0),"frustration_count":baseline.get("frustration_count",0),"p95_queue_ms":baseline.get("p95_queue_ms",0),"p95_duration_ms":baseline.get("p95_duration_ms",0),"metrics":{"request_success_rate":baseline.get("request_success_rate",0),"frustration_per_request":baseline.get("frustration_per_request",0),"quality_supported_request_rate":baseline.get("quality_supported_request_rate",0),"verified_request_success_per_cpu_minute":baseline.get("verified_request_success_per_cpu_minute",0)}}
    current.state="superseded"; row=PublicRollout(version=_next_rollout_version(db),state="active",exposure_percent=exposure_percent,assignment_salt=current.assignment_salt,baseline_metrics=baseline,latest_metrics=evaluation,guardrails=current.guardrails,decision={"status":"advanced","from_percent":current.exposure_percent,"to_percent":exposure_percent,"evaluation":evaluation},previous_rollout_id=current.id,created_by=actor_id,approved_by=actor_id,opened_at=utcnow(),completed_at=utcnow() if exposure_percent==100 else None); db.add(row); db.flush(); return row


def freeze_rollout(row:PublicRollout,*,evaluation:dict[str,Any],actor_id:str|None=None)->None:
    if row.state!="active":return
    row.state="paused";row.paused_at=utcnow();row.approved_by=actor_id or row.approved_by;row.decision={"status":"paused","evaluation":evaluation}


def rollback_rollout(db:Session,current:PublicRollout,*,actor_id:str|None)->PublicRollout:
    target=db.get(PublicRollout,current.previous_rollout_id) if current.previous_rollout_id else None;target_percent=target.exposure_percent if target is not None else 0;current.state="rolled_back";current.rolled_back_at=utcnow();row=PublicRollout(version=_next_rollout_version(db),state="active" if target_percent>0 else "planned",exposure_percent=target_percent,assignment_salt=current.assignment_salt,baseline_metrics=dict(target.baseline_metrics or {}) if target is not None else dict(current.baseline_metrics or {}),latest_metrics=current.latest_metrics,guardrails=current.guardrails,decision={"status":"rollback","from_percent":current.exposure_percent,"to_percent":target_percent,"rolled_back_version":current.version},previous_rollout_id=current.id,created_by=actor_id,approved_by=actor_id,opened_at=utcnow() if target_percent>0 else None);db.add(row);db.flush();return row
