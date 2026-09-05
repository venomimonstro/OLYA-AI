from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ProjectMemory, User
from app.schemas.memory import MemoryResponse, MemoryUpsert
from app.services.access import require_project_role
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/projects/{project_id}/memory", tags=["memory"])


@router.get("", response_model=list[MemoryResponse])
def list_memory(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MemoryResponse]:
    require_project_role(db, user, project_id, "viewer")
    items = db.scalars(
        select(ProjectMemory).where(ProjectMemory.project_id == project_id).order_by(ProjectMemory.key)
    ).all()
    return [MemoryResponse.model_validate(i, from_attributes=True) for i in items]


@router.put("", response_model=MemoryResponse)
def upsert_memory(payload: MemoryUpsert, project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MemoryResponse:
    require_project_role(db, user, project_id, "member")
    key = payload.key.strip()
    item = db.scalar(select(ProjectMemory).where(ProjectMemory.project_id == project_id, ProjectMemory.key == key))
    if item is None:
        item = ProjectMemory(project_id=project_id, key=key, value=payload.value.strip(), source="user", created_by=user.id)
        db.add(item)
    else:
        item.value = payload.value.strip()
    db.commit()
    db.refresh(item)
    return MemoryResponse.model_validate(item, from_attributes=True)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    require_project_role(db, user, project_id, "member")
    item = db.get(ProjectMemory, memory_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    db.delete(item)
    db.commit()
