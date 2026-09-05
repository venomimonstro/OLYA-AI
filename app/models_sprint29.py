from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

def utcnow(): return datetime.now(timezone.utc)
def new_id(): return str(uuid4())

class BetaParticipant(Base):
    __tablename__='beta_participants'
    __table_args__=(UniqueConstraint('user_id','cohort',name='uq_beta_participant_user_cohort'),Index('ix_beta_participant_cohort_state','cohort','state'),Index('ix_beta_participant_enrolled','enrolled_at'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    cohort:Mapped[str]=mapped_column(String(64),index=True)
    state:Mapped[str]=mapped_column(String(24),default='active',index=True)
    source:Mapped[str]=mapped_column(String(64),default='manual')
    metadata_json:Mapped[dict]=mapped_column(JSON,default=dict)
    enrolled_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)

class BetaSnapshot(Base):
    __tablename__='beta_snapshots'
    __table_args__=(Index('ix_beta_snapshot_cohort_created','cohort','created_at'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    cohort:Mapped[str]=mapped_column(String(64),index=True)
    window_days:Mapped[int]=mapped_column(Integer,default=30)
    enrolled_count:Mapped[int]=mapped_column(Integer,default=0)
    activated_count:Mapped[int]=mapped_column(Integer,default=0)
    d1_eligible_count:Mapped[int]=mapped_column(Integer,default=0)
    d1_retained_count:Mapped[int]=mapped_column(Integer,default=0)
    d7_eligible_count:Mapped[int]=mapped_column(Integer,default=0)
    d7_retained_count:Mapped[int]=mapped_column(Integer,default=0)
    request_count:Mapped[int]=mapped_column(Integer,default=0)
    success_count:Mapped[int]=mapped_column(Integer,default=0)
    task_count:Mapped[int]=mapped_column(Integer,default=0)
    completed_task_count:Mapped[int]=mapped_column(Integer,default=0)
    frustration_count:Mapped[int]=mapped_column(Integer,default=0)
    compute_minutes_total:Mapped[float]=mapped_column(Float,default=0.0)
    compute_minutes_per_active_user:Mapped[float]=mapped_column(Float,default=0.0)
    p95_duration_ms:Mapped[int]=mapped_column(Integer,default=0)
    p95_queue_ms:Mapped[int]=mapped_column(Integer,default=0)
    metrics:Mapped[dict]=mapped_column(JSON,default=dict)
    readiness:Mapped[dict]=mapped_column(JSON,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,index=True)
