from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Project, ProjectMember, User


ROLE_RANK = {"viewer": 10, "member": 20, "manager": 30, "owner": 40}


def project_role(db: Session, user_id: str, project: Project) -> str | None:
    if project.owner_id == user_id:
        return "owner"
    return db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
        )
    )


def require_project_role(db: Session, user: User, project_id: str, minimum: str = "viewer") -> tuple[Project, str]:
    project = db.get(Project, project_id)
    if project is None:
        # Do not reveal whether an inaccessible project exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    role = project_role(db, user.id, project)
    if role is None or ROLE_RANK.get(role, 0) < ROLE_RANK[minimum]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project, role


def list_accessible_projects(db: Session, user_id: str) -> list[Project]:
    stmt = (
        select(Project)
        .outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
        .where(or_(Project.owner_id == user_id, ProjectMember.user_id == user_id))
        .distinct()
        .order_by(Project.updated_at.desc())
    )
    return list(db.scalars(stmt).all())
