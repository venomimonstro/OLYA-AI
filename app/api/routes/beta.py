from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BetaParticipant, BetaSnapshot, User
from app.services.admin import audit, require_admin
from app.services.beta import build_beta_snapshot, snapshot_dict

router=APIRouter(prefix='/v1/admin/beta',tags=['admin-beta'])
class Enroll(BaseModel):
    cohort:str=Field(default='closed-beta-1',min_length=1,max_length=64); source:str=Field(default='manual',max_length=64); metadata:dict=Field(default_factory=dict)
class State(BaseModel): state:str

@router.post('/participants/{user_id}')
def enroll(user_id:str,payload:Enroll,admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    if db.get(User,user_id) is None: raise HTTPException(404,'User not found')
    row=db.scalar(select(BetaParticipant).where(BetaParticipant.user_id==user_id,BetaParticipant.cohort==payload.cohort))
    if row is None: row=BetaParticipant(user_id=user_id,cohort=payload.cohort,source=payload.source,metadata_json=payload.metadata); db.add(row); db.flush()
    else: row.state='active'; row.source=payload.source; row.metadata_json=payload.metadata
    audit(db,admin,'beta.enroll','beta_participant',row.id,{'cohort':payload.cohort}); db.commit(); return {'id':row.id,'user_id':row.user_id,'cohort':row.cohort,'state':row.state,'source':row.source,'metadata':row.metadata_json,'enrolled_at':row.enrolled_at}

@router.patch('/participants/{participant_id}')
def state(participant_id:str,payload:State,admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    if payload.state not in {'active','paused','removed'}: raise HTTPException(422,'Unsupported beta participant state')
    row=db.get(BetaParticipant,participant_id)
    if row is None: raise HTTPException(404,'Beta participant not found')
    row.state=payload.state; audit(db,admin,'beta.state','beta_participant',row.id,{'state':row.state}); db.commit(); return {'id':row.id,'state':row.state}

@router.get('/participants')
def participants(cohort:str='closed-beta-1',limit:int=Query(default=100,ge=1,le=500),admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    _=admin; rows=db.scalars(select(BetaParticipant).where(BetaParticipant.cohort==cohort).order_by(BetaParticipant.enrolled_at.desc()).limit(limit)).all(); return [{'id':x.id,'user_id':x.user_id,'state':x.state,'source':x.source,'enrolled_at':x.enrolled_at} for x in rows]

@router.post('/snapshots')
def snapshot(cohort:str='closed-beta-1',window_days:int=Query(default=30,ge=1,le=180),admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    row=build_beta_snapshot(db,cohort=cohort,window_days=window_days); audit(db,admin,'beta.snapshot','beta_snapshot',row.id,{'cohort':cohort}); db.commit(); db.refresh(row); return snapshot_dict(row)

@router.get('/snapshots')
def snapshots(cohort:str='closed-beta-1',limit:int=Query(default=50,ge=1,le=200),admin:User=Depends(require_admin),db:Session=Depends(get_db)):
    _=admin; return [snapshot_dict(x) for x in db.scalars(select(BetaSnapshot).where(BetaSnapshot.cohort==cohort).order_by(BetaSnapshot.created_at.desc()).limit(limit)).all()]
