from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

def utcnow(): return datetime.now(timezone.utc)
def new_id(): return str(uuid4())

class SystemCheckpoint(Base):
    __tablename__='system_checkpoints'
    __table_args__=(UniqueConstraint('key',name='uq_system_checkpoint_key'),Index('ix_system_checkpoint_status_checked','status','last_checked_at'),Index('ix_system_checkpoint_subsystem_status','subsystem','status'))
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    key:Mapped[str]=mapped_column(String(160),index=True)
    subsystem:Mapped[str]=mapped_column(String(80),index=True)
    dependency:Mapped[str]=mapped_column(String(160),default='')
    status:Mapped[str]=mapped_column(String(16),default='unknown',index=True)
    severity:Mapped[str]=mapped_column(String(16),default='info',index=True)
    critical:Mapped[bool]=mapped_column(Boolean,default=False,index=True)
    latency_ms:Mapped[int]=mapped_column(Integer,default=0)
    message:Mapped[str]=mapped_column(String(500),default='')
    details:Mapped[dict]=mapped_column(JSON,default=dict)
    consecutive_failures:Mapped[int]=mapped_column(Integer,default=0)
    last_ok_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    last_checked_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,onupdate=utcnow)

class SystemHealthSnapshot(Base):
    __tablename__='system_health_snapshots'
    __table_args__=(Index('ix_system_health_snapshot_created','created_at'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    overall_status:Mapped[str]=mapped_column(String(16),index=True)
    score:Mapped[int]=mapped_column(Integer,default=0)
    stable_count:Mapped[int]=mapped_column(Integer,default=0)
    degraded_count:Mapped[int]=mapped_column(Integer,default=0)
    failed_count:Mapped[int]=mapped_column(Integer,default=0)
    critical_failed_count:Mapped[int]=mapped_column(Integer,default=0)
    checks:Mapped[list[dict]]=mapped_column(JSON,default=list)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,index=True)
