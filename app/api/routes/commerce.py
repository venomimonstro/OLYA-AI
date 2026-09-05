from __future__ import annotations

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, Organization, OrganizationBudget, OrganizationMember, ResourceExpenseEvent, User
from app.schemas.commerce import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, BudgetPut, BudgetRead, OrganizationCreate, OrganizationMemberRead, OrganizationMemberUpsert, OrganizationRead, PaymentIngest, PaymentRead
from app.services.admin import audit, require_admin
from app.services.auth import get_current_user, normalize_email
from app.services.commerce import apply_plan_to_quota, budget_state, create_api_key, ingest_payment, list_organizations, measured_user_resources, normalize_slug, organization_role, payment_reconciliation, plan_catalog, require_organization_role

router=APIRouter(prefix="/v1/commerce",tags=["commerce"])

def _org_response(db,user,org): return OrganizationRead(id=org.id,owner_id=org.owner_id,name=org.name,slug=org.slug,plan=org.plan,role=organization_role(db,user.id,org) or "member",created_at=org.created_at,updated_at=org.updated_at)
def _require_org(db,user,org_id,minimum="member"):
    try:return require_organization_role(db,user,org_id,minimum)
    except LookupError as exc: raise HTTPException(status_code=404,detail="Organization not found") from exc

@router.get('/plans')
def plans(request:Request): return plan_catalog(request.app.state.settings)

@router.get('/usage')
def usage(request:Request,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    measured=measured_user_resources(db,user.id,request.app.state.settings)
    from app.services.quota import get_or_create_quota
    quota=get_or_create_quota(db,user,request.app.state.settings); policy=next((x for x in plan_catalog(request.app.state.settings) if x['name']==quota.plan),None)
    measured['plan']=quota.plan; measured['plan_resource_budget_microunits']=policy['resource_budget_microunits'] if policy else 0; measured['remaining_resource_microunits']=max(0,measured['plan_resource_budget_microunits']-measured['total_cost_microunits']); db.commit(); return measured

@router.post('/organizations',response_model=OrganizationRead,status_code=status.HTTP_201_CREATED)
def create_org(payload:OrganizationCreate,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    try: slug=normalize_slug(payload.slug)
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    org=Organization(owner_id=user.id,name=payload.name.strip(),slug=slug,plan='business'); db.add(org)
    try: db.commit()
    except IntegrityError as exc: db.rollback(); raise HTTPException(status_code=409,detail='Organization slug already exists') from exc
    db.refresh(org); return _org_response(db,user,org)

@router.get('/organizations',response_model=list[OrganizationRead])
def orgs(user:User=Depends(get_current_user),db:Session=Depends(get_db)): return [_org_response(db,user,x) for x in list_organizations(db,user.id)]

@router.get('/organizations/{organization_id}/members',response_model=list[OrganizationMemberRead])
def members(organization_id:str,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    org,_=_require_org(db,user,organization_id); owner=db.get(User,org.owner_id); result=[OrganizationMemberRead(user_id=owner.id,email=owner.email,display_name=owner.display_name,role='owner')] if owner else []
    rows=db.execute(select(OrganizationMember,User).join(User,User.id==OrganizationMember.user_id).where(OrganizationMember.organization_id==org.id).order_by(User.email)).all(); result.extend(OrganizationMemberRead(user_id=u.id,email=u.email,display_name=u.display_name,role=m.role) for m,u in rows); return result

@router.put('/organizations/{organization_id}/members',response_model=OrganizationMemberRead)
def upsert_member(organization_id:str,payload:OrganizationMemberUpsert,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    org,role=_require_org(db,user,organization_id,'manager')
    if role!='owner': raise HTTPException(status_code=403,detail='Only organization owner can manage members')
    target=db.scalar(select(User).where(User.email==normalize_email(payload.email)))
    if target is None: raise HTTPException(status_code=404,detail='User not found')
    if target.id==org.owner_id: raise HTTPException(status_code=409,detail='Organization owner role cannot be replaced')
    row=db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id==org.id,OrganizationMember.user_id==target.id))
    if row is None: row=OrganizationMember(organization_id=org.id,user_id=target.id,role=payload.role); db.add(row)
    else: row.role=payload.role
    db.commit(); return OrganizationMemberRead(user_id=target.id,email=target.email,display_name=target.display_name,role=row.role)

@router.put('/organizations/{organization_id}/budget',response_model=BudgetRead)
def put_budget(organization_id:str,payload:BudgetPut,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    org,_=_require_org(db,user,organization_id,'manager'); row=db.scalar(select(OrganizationBudget).where(OrganizationBudget.organization_id==org.id,OrganizationBudget.month==payload.month))
    if row is None: row=OrganizationBudget(organization_id=org.id,month=payload.month,created_by=user.id); db.add(row)
    row.limit_microunits=payload.limit_microunits; row.alert_percent=payload.alert_percent; row.hard_limit=payload.hard_limit; db.commit(); db.refresh(row)
    return BudgetRead(id=row.id,organization_id=row.organization_id,month=row.month,limit_microunits=row.limit_microunits,alert_percent=row.alert_percent,hard_limit=row.hard_limit,**budget_state(db,row))

@router.get('/organizations/{organization_id}/expenses')
def expenses(organization_id:str,month:str|None=Query(default=None,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),limit:int=Query(default=100,ge=1,le=500),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    org,_=_require_org(db,user,organization_id,'manager'); rows=db.scalars(select(ResourceExpenseEvent).where(ResourceExpenseEvent.organization_id==org.id).order_by(ResourceExpenseEvent.created_at.desc()).limit(limit)).all()
    if month: rows=[x for x in rows if x.created_at.strftime('%Y-%m')==month]
    return [{'id':x.id,'user_id':x.user_id,'project_id':x.project_id,'api_key_id':x.api_key_id,'resource_kind':x.resource_kind,'quantity_ms':x.quantity_ms,'cost_microunits':x.cost_microunits,'source_kind':x.source_kind,'source_id':x.source_id,'metadata':x.metadata_json,'created_at':x.created_at} for x in rows]

@router.post('/api-keys',response_model=ApiKeyCreated,status_code=status.HTTP_201_CREATED)
def new_key(payload:ApiKeyCreate,request:Request,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    try: token,row=create_api_key(db,user,request.app.state.settings,name=payload.name,scopes=payload.scopes,organization_id=payload.organization_id,rate_limit_per_minute=payload.rate_limit_per_minute,expires_at=payload.expires_at)
    except (ValueError,LookupError) as exc: db.rollback(); raise HTTPException(status_code=422 if isinstance(exc,ValueError) else 404,detail=str(exc)) from exc
    db.commit(); db.refresh(row); return ApiKeyCreated(id=row.id,name=row.name,prefix=row.prefix,token=token,scopes=row.scopes,rate_limit_per_minute=row.rate_limit_per_minute,organization_id=row.organization_id,expires_at=row.expires_at,created_at=row.created_at)

@router.get('/api-keys',response_model=list[ApiKeyRead])
def keys(user:User=Depends(get_current_user),db:Session=Depends(get_db)): return db.scalars(select(ApiKey).where(ApiKey.owner_id==user.id).order_by(ApiKey.created_at.desc())).all()

@router.delete('/api-keys/{api_key_id}',status_code=status.HTTP_204_NO_CONTENT)
def revoke(api_key_id:str,user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    row=db.get(ApiKey,api_key_id)
    if row is None or row.owner_id!=user.id: raise HTTPException(status_code=404,detail='API key not found')
    if row.status!='revoked': row.status='revoked'; row.revoked_at=datetime.now(timezone.utc)
    db.commit()

@router.post('/payments/ingest',response_model=PaymentRead)
def payment(payload:PaymentIngest,request:Request,x_x1_payment_secret:str=Header(default='',alias='X-X1-Payment-Secret'),db:Session=Depends(get_db)):
    expected=request.app.state.settings.payment_ingest_secret
    if not expected or not hmac.compare_digest(x_x1_payment_secret,expected): raise HTTPException(status_code=403,detail='Payment ingestion disabled or secret invalid')
    if payload.user_id and db.get(User,payload.user_id) is None: raise HTTPException(status_code=404,detail='Payment user not found')
    if payload.organization_id and db.get(Organization,payload.organization_id) is None: raise HTTPException(status_code=404,detail='Payment organization not found')
    try: row,_=ingest_payment(db,payload.model_dump(mode='json'))
    except ValueError as exc: db.rollback(); raise HTTPException(status_code=409,detail=str(exc)) from exc
    db.commit(); db.refresh(row); return row

@router.get('/payments/reconciliation')
def reconcile(admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    result=payment_reconciliation(db); audit(db,admin,'commerce.payment_reconciliation','payment_record','',result); db.commit(); return result

@router.post('/users/{user_id}/plan/{plan}')
def set_plan(user_id:str,plan:str,admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    target=db.get(User,user_id)
    if target is None: raise HTTPException(status_code=404,detail='User not found')
    try: quota=apply_plan_to_quota(db,target,plan)
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc)) from exc
    audit(db,admin,'commerce.plan_set','user',target.id,{'plan':plan}); db.commit(); return {'user_id':target.id,'plan':quota.plan,'monthly_compute_seconds_limit':quota.monthly_compute_seconds_limit,'max_concurrent_inference':quota.max_concurrent_inference,'max_concurrent_jobs':quota.max_concurrent_jobs}
