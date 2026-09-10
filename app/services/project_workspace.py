from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Conversation, DevelopmentPlan, Project, ProjectFile, ProjectMemory, Task
from app.schemas.project_workspace import (
    ProjectConversationPreview,
    ProjectDevelopmentPreview,
    ProjectFilePreview,
    ProjectMemoryPreview,
    ProjectTaskPreview,
    ProjectWorkspaceCounts,
    ProjectWorkspaceResponse,
)
from app.schemas.projects import ProjectResponse


PREVIEW_LIMIT = 8
MEMORY_PREVIEW_LIMIT = 50


def _count(db: Session, model, project_id: str, *conditions) -> int:
    statement = select(func.count()).select_from(model).where(model.project_id == project_id, *conditions)
    return int(db.scalar(statement) or 0)


def build_project_workspace(db: Session, project: Project, role: str) -> ProjectWorkspaceResponse:
    """Build one bounded, project-scoped product snapshot without loading heavy content."""
    conversations = list(
        db.scalars(
            select(Conversation)
            .where(Conversation.project_id == project.id)
            .order_by(Conversation.updated_at.desc())
            .limit(PREVIEW_LIMIT)
        ).all()
    )
    files = list(
        db.scalars(
            select(ProjectFile)
            .where(ProjectFile.project_id == project.id, ProjectFile.is_current.is_(True))
            .order_by(ProjectFile.created_at.desc())
            .limit(PREVIEW_LIMIT)
        ).all()
    )
    memories = list(
        db.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project.id)
            .order_by(ProjectMemory.updated_at.desc())
            .limit(MEMORY_PREVIEW_LIMIT)
        ).all()
    )
    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.project_id == project.id)
            .order_by(Task.updated_at.desc())
            .limit(PREVIEW_LIMIT)
        ).all()
    )
    development = db.scalar(select(DevelopmentPlan).where(DevelopmentPlan.project_id == project.id).limit(1))

    return ProjectWorkspaceResponse(
        project=ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            instructions=project.instructions,
            role=role,
            created_at=project.created_at,
            updated_at=project.updated_at,
        ),
        counts=ProjectWorkspaceCounts(
            conversations=_count(db, Conversation, project.id),
            files=_count(db, ProjectFile, project.id, ProjectFile.is_current.is_(True)),
            memories=_count(db, ProjectMemory, project.id),
            tasks=_count(db, Task, project.id),
        ),
        recent_conversations=[
            ProjectConversationPreview(id=item.id, title=item.title, updated_at=item.updated_at)
            for item in conversations
        ],
        recent_files=[
            ProjectFilePreview(
                id=item.id,
                logical_name=item.logical_name,
                status=item.status,
                size_bytes=item.size_bytes,
                created_at=item.created_at,
            )
            for item in files
        ],
        memories=[
            ProjectMemoryPreview(
                id=item.id,
                key=item.key,
                value=item.value,
                updated_at=item.updated_at,
            )
            for item in memories
        ],
        recent_tasks=[
            ProjectTaskPreview(
                id=item.id,
                title=item.title,
                status=item.status,
                current_step=item.current_step,
                updated_at=item.updated_at,
            )
            for item in tasks
        ],
        development=(
            ProjectDevelopmentPreview(
                id=development.id,
                title=development.title,
                status=development.status,
                current_sprint_ordinal=development.current_sprint_ordinal,
                updated_at=development.updated_at,
            )
            if development is not None
            else None
        ),
    )
