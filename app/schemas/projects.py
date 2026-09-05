from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ProjectRole = Literal["viewer", "member", "manager"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    instructions: str = Field(default="", max_length=50_000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20_000)
    instructions: str | None = Field(default=None, max_length=50_000)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    role: str
    created_at: datetime
    updated_at: datetime


class MemberUpsert(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: ProjectRole = "member"


class MemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
