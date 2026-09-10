from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OnboardingMilestones(BaseModel):
    chat_started: datetime | None = None
    successful_answer: datetime | None = None
    project_created: datetime | None = None
    file_uploaded: datetime | None = None


class OnboardingStatus(BaseModel):
    visible: bool
    completed: bool
    dismissed: bool
    show_again: bool
    started_at: datetime
    completed_at: datetime | None = None
    dismissed_at: datetime | None = None
    reopened_at: datetime | None = None
    recommended_action: str | None = None
    milestones: OnboardingMilestones
