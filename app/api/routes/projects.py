from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Project, ProjectMember, User
from app.schemas.projects import MemberResponse, MemberUpsert, ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.access import list_accessible_projects, project_role, require_project_role
from app.services.auth import get_current_user, normalize_email

router = APIRouter(prefix="/v1/projects", tags=["projects"])


def _response(db: Session, user: User, project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        instructions=project.instructions,
        role=project_role(db, user.id, project) or "viewer",
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectResponse:
    project = Project(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description,
        instructions=payload.instructions,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _response(db, user, project)


@router.get("", response_model=list[ProjectResponse])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ProjectResponse]:
    return [_response(db, user, project) for project in list_accessible_projects(db, user.id)]


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectResponse:
    project, _ = require_project_role(db, user, project_id, "viewer")
    return _response(db, user, project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(payload: ProjectUpdate, project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectResponse:
    project, _ = require_project_role(db, user, project_id, "manager")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for key, value in changes.items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return _response(db, user, project)


@router.get("/{project_id}/members", response_model=list[MemberResponse])
def list_members(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MemberResponse]:
    project, _ = require_project_role(db, user, project_id, "viewer")
    owner = db.get(User, project.owner_id)
    result = [MemberResponse(user_id=owner.id, email=owner.email, display_name=owner.display_name, role="owner")] if owner else []
    members = db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.email)
    ).all()
    result.extend(MemberResponse(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role) for m, u in members)
    return result


@router.put("/{project_id}/members", response_model=MemberResponse)
def upsert_member(payload: MemberUpsert, project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MemberResponse:
    project, role = require_project_role(db, user, project_id, "manager")
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project owner can manage members")
    target = db.scalar(select(User).where(User.email == normalize_email(payload.email)))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == project.owner_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Owner role cannot be replaced")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
    if member is None:
        member = ProjectMember(project_id=project.id, user_id=target.id, role=payload.role)
        db.add(member)
    else:
        member.role = payload.role
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Membership update conflict") from exc
    return MemberResponse(user_id=target.id, email=target.email, display_name=target.display_name, role=member.role)


@router.delete("/{project_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(member_user_id: str, project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    project, role = require_project_role(db, user, project_id, "manager")
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project owner can manage members")
    if member_user_id == project.owner_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Owner cannot be removed")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == member_user_id))
    if member is not None:
        db.delete(member)
        db.commit()
