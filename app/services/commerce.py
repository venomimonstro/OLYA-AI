from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import String, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ApiKey, ApiRequestTelemetry, ImageGeneration, Organization, OrganizationBudget, OrganizationMember, PaymentRecord, ProjectSandboxRun, ResourceExpenseEvent, UsageEvent, User, UserQuota, utcnow
from app.services.auth import token_digest

ORG_ROLE_RANK={"member":10,"manager":20,"owner":30}
ALLOWED_API_SCOPES={"chat","contexts:read","contexts:write","telemetry:read"}

@dataclass(frozen=True)
class PlanPolicy:
    name:str; resource_budget_microunits:int; monthly_cpu_seconds:int; max_concurrent_inference:int; max_concurrent_jobs:int; organization_enabled:bool=False

PLAN_POLICIES={
 "free":PlanPolicy("free",600_000,600,1,1),"x1":PlanPolicy("x1",7_200_000,7_200,1,2),
 "pro":PlanPolicy("pro",36_000_000,36_000,2,4),"max":PlanPolicy("max",120_000_000,120_000,4,8),
 "business":PlanPolicy("business",300_000_000,300_000,8,16,True),}

def month_start(now=None):
    value=now or datetime.now(timezone.utc)
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.replace(day=1,hour=0,minute=0,second=0,microsecond=0)

def resource_rates(settings):
    return {"cpu":settings.commerce_cpu_microunits_per_second,"gpu":settings.commerce_gpu_microunits_per_second,"image_worker":settings.commerce_image_worker_microunits_per_second,"sandbox":settings.commerce_sandbox_microunits_per_second}

def price_resource_ms(settings,resource_kind,quantity_ms):
    rate=resource_rates(settings).get(resource_kind)
    if rate is None: raise ValueError(f"Unsupported resource kind: {resource_kind}")
    return (max(0,int(quantity_ms))*rate+999)//1000

def plan_resource_budget(settings,policy): return policy.monthly_cpu_seconds*settings.commerce_cpu_microunits_per_second

def plan_catalog(settings):
    rates=resource_rates(settings)
    return [{"name":p.name,"resource_budget_microunits":plan_resource_budget(settings,p),"monthly_cpu_seconds":p.monthly_cpu_seconds,"max_concurrent_inference":p.max_concurrent_inference,"max_concurrent_jobs":p.max_concurrent_jobs,"organization_enabled":p.organization_enabled,"resource_rates_microunits_per_second":rates} for p in PLAN_POLICIES.values()]

def _aware(v): return None if v is None else v if v.tzinfo else v.replace(tzinfo=timezone.utc)
def _elapsed_ms(a,b):
    a,b=_aware(a),_aware(b)
    return 0 if not a or not b or b<=a else int((b-a).total_seconds()*1000)

def measured_user_resources(db,user_id,settings,now=None):
    since=month_start(now)
    cpu_ms=int(db.scalar(select(func.coalesce(func.sum(UsageEvent.inference_ms),0)).where(UsageEvent.user_id==user_id,UsageEvent.created_at>=since)) or 0)
    images=db.scalars(select(ImageGeneration).where(ImageGeneration.user_id==user_id,ImageGeneration.created_at>=since,ImageGeneration.started_at.is_not(None),ImageGeneration.finished_at.is_not(None))).all()
    image_ms=sum(_elapsed_ms(x.started_at,x.finished_at) for x in images)
    sandboxes=db.scalars(select(ProjectSandboxRun).where(ProjectSandboxRun.created_by==user_id,ProjectSandboxRun.created_at>=since,ProjectSandboxRun.started_at.is_not(None),ProjectSandboxRun.completed_at.is_not(None))).all()
    sandbox_ms=sum(_elapsed_ms(x.started_at,x.completed_at) for x in sandboxes)
    gpu_ms=int(db.scalar(select(func.coalesce(func.sum(ResourceExpenseEvent.quantity_ms),0)).where(ResourceExpenseEvent.user_id==user_id,ResourceExpenseEvent.created_at>=since,ResourceExpenseEvent.resource_kind=="gpu")) or 0)
    usage={"cpu":cpu_ms,"gpu":gpu_ms,"image_worker":image_ms,"sandbox":sandbox_ms}; cost={k:price_resource_ms(settings,k,v) for k,v in usage.items()}
    return {"month":since.strftime("%Y-%m"),"usage_ms":usage,"cost_microunits":cost,"total_cost_microunits":sum(cost.values())}

def apply_plan_to_quota(db,user,plan):
    policy=PLAN_POLICIES.get(plan)
    if policy is None: raise ValueError("Unknown plan")
    quota=db.get(UserQuota,user.id) or UserQuota(user_id=user.id); db.add(quota)
    quota.plan=policy.name; quota.monthly_compute_seconds_limit=policy.monthly_cpu_seconds; quota.max_concurrent_inference=policy.max_concurrent_inference; quota.max_concurrent_jobs=policy.max_concurrent_jobs
    return quota

def normalize_slug(value):
    slug=re.sub(r"[^a-z0-9-]+","-",value.strip().lower()).strip("-"); slug=re.sub(r"-+","-",slug)
    if len(slug)<2: raise ValueError("Organization slug is invalid")
    return slug[:120]

def organization_role(db,user_id,org):
    if org.owner_id==user_id:return "owner"
    row=db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id==org.id,OrganizationMember.user_id==user_id)); return row.role if row else None

def require_organization_role(db,user,organization_id,minimum="member"):
    org=db.get(Organization,organization_id)
    if org is None: raise LookupError("Organization not found")
    role=organization_role(db,user.id,org)
    if role is None or ORG_ROLE_RANK.get(role,0)<ORG_ROLE_RANK[minimum]: raise LookupError("Organization not found")
    return org,role

def list_organizations(db,user_id): return list(db.scalars(select(Organization).outerjoin(OrganizationMember,OrganizationMember.organization_id==Organization.id).where(or_(Organization.owner_id==user_id,OrganizationMember.user_id==user_id)).distinct().order_by(Organization.updated_at.desc())).all())
def budget_spend(db,organization_id,month): return int(db.scalar(select(func.coalesce(func.sum(ResourceExpenseEvent.cost_microunits),0)).where(ResourceExpenseEvent.organization_id==organization_id,func.substr(func.cast(ResourceExpenseEvent.created_at,String),1,7)==month)) or 0)
def budget_state(db,budget):
    spent=budget_spend(db,budget.organization_id,budget.month); limit=max(0,budget.limit_microunits)
    return {"spent_microunits":spent,"remaining_microunits":max(0,limit-spent),"utilization_percent":0.0 if limit<=0 else round(spent/limit*100,2)}
def ensure_organization_budget(db,organization_id,incremental_cost,now=None):
    month=month_start(now).strftime("%Y-%m"); budget=db.scalar(select(OrganizationBudget).where(OrganizationBudget.organization_id==organization_id,OrganizationBudget.month==month))
    if budget is None or not budget.hard_limit or budget.limit_microunits<=0:return
    if budget_spend(db,organization_id,month)+max(0,incremental_cost)>budget.limit_microunits: raise RuntimeError("Organization monthly resource budget exhausted")
def create_api_key(db,user,settings,*,name,scopes,organization_id,rate_limit_per_minute,expires_at):
    scopes=sorted(set(scopes))
    if not scopes or not set(scopes).issubset(ALLOWED_API_SCOPES): raise ValueError("Invalid API key scopes")
    if organization_id: require_organization_role(db,user,organization_id,"manager")
    limit=min(max(1,rate_limit_per_minute or settings.api_default_rate_limit_per_minute),settings.api_max_rate_limit_per_minute)
    raw=secrets.token_urlsafe(40); prefix=secrets.token_hex(4); token=f"x1k_{prefix}_{raw}"
    row=ApiKey(owner_id=user.id,organization_id=organization_id,name=name.strip(),prefix=prefix,secret_hash=token_digest(token),scopes=scopes,rate_limit_per_minute=limit,expires_at=expires_at); db.add(row); db.flush(); return token,row
def payment_payload_hash(payload): return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def ingest_payment(db,payload):
    digest=payment_payload_hash(payload); existing=db.scalar(select(PaymentRecord).where(PaymentRecord.provider==payload["provider"],PaymentRecord.idempotency_key==payload["idempotency_key"]))
    if existing:
        if existing.payload_hash!=digest: raise ValueError("Idempotency key already used with a different payment payload")
        return existing,False
    event=db.scalar(select(PaymentRecord).where(PaymentRecord.provider==payload["provider"],PaymentRecord.provider_event_id==payload["provider_event_id"]))
    if event:
        if event.payload_hash!=digest: raise ValueError("Provider event already exists with a different payload")
        return event,False
    row=PaymentRecord(user_id=payload.get("user_id"),organization_id=payload.get("organization_id"),provider=payload["provider"],provider_event_id=payload["provider_event_id"],idempotency_key=payload["idempotency_key"],kind=payload["kind"],amount_minor=payload["amount_minor"],currency=payload["currency"].upper(),payload_hash=digest,metadata_json=payload.get("metadata") or {}); db.add(row); db.flush(); return row,True
def payment_reconciliation(db):
    rows=db.scalars(select(PaymentRecord).order_by(PaymentRecord.created_at)).all(); totals={}; unreconciled=0
    for row in rows:
        totals[row.currency]=totals.get(row.currency,0)+(-1 if row.kind=="refund" else 1)*row.amount_minor; unreconciled+=row.reconciled_at is None
    return {"records":len(rows),"unreconciled":unreconciled,"net_amount_minor_by_currency":totals}
def record_telemetry(db,*,api_key,endpoint,request_id,status_code,latency_ms,quality_status,context_id,project_id,resource_usage,cost_microunits):
    row=ApiRequestTelemetry(api_key_id=api_key.id,user_id=api_key.owner_id,organization_id=api_key.organization_id,context_id=context_id,project_id=project_id,endpoint=endpoint,request_id=request_id,status_code=status_code,latency_ms=max(0,latency_ms),quality_status=quality_status,cost_microunits=max(0,cost_microunits),resource_usage=resource_usage); db.add(row)
    if cost_microunits>0: db.add(ResourceExpenseEvent(user_id=api_key.owner_id,organization_id=api_key.organization_id,project_id=project_id,api_key_id=api_key.id,resource_kind="cpu",quantity_ms=int(resource_usage.get("cpu_ms",0)),cost_microunits=cost_microunits,source_kind="api_request",source_id=request_id,metadata_json={"endpoint":endpoint,"quality_status":quality_status}))
    return row
