from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FileChunk, ProjectFile, User
from app.schemas.files import FileChunkRead, FileRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.files import (
    chunk_segments,
    next_file_version,
    parse_content,
    retrieve_chunks,
    safe_filename,
    sha256_bytes,
    storage_path,
)

router = APIRouter(prefix="/v1/projects", tags=["files"])


@router.post("/{project_id}/files", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=240),
    logical_name: str | None = Query(default=None, max_length=240),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectFile:
    _project, role = require_project_role(db, user, project_id, "member")
    settings = request.app.state.settings
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(status_code=413, detail="File is too large")

    name = safe_filename(logical_name or filename)
    current_same_name = db.scalar(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.logical_name == name,
            ProjectFile.is_current.is_(True),
        )
    )
    if current_same_name is not None and role not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="Only project manager can replace an existing file")

    digest = sha256_bytes(content)
    existing = db.scalar(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.logical_name == name,
            ProjectFile.content_sha256 == digest,
            ProjectFile.is_current.is_(True),
        )
    )
    if existing is not None:
        return existing

    version = next_file_version(db, project_id, name)
    file = ProjectFile(
        project_id=project_id,
        uploaded_by=user.id,
        logical_name=name,
        original_name=safe_filename(filename),
        version=version,
        content_sha256=digest,
        media_type=request.headers.get("content-type") or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=len(content),
        storage_path="",
        status="processing",
    )
    db.add(file)
    db.flush()

    root = Path(settings.file_storage_path).resolve()
    destination = storage_path(root, project_id, file.id, version, filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    file.storage_path = str(destination)

    try:
        segments = parse_content(
            filename,
            content,
            max_pdf_pages=settings.max_pdf_pages,
            max_docx_unpacked_bytes=settings.max_docx_unpacked_bytes,
        )
        chunks = chunk_segments(segments, max_chars=settings.file_chunk_chars, overlap_chars=settings.file_chunk_overlap_chars)
        if not chunks:
            raise ValueError("No readable text found")
        for ordinal, item in enumerate(chunks):
            chunk_digest = sha256_bytes(item.text.encode("utf-8"))
            db.add(
                FileChunk(
                    file_id=file.id,
                    ordinal=ordinal,
                    page_number=item.page_number,
                    content=item.text,
                    content_sha256=chunk_digest,
                    char_count=len(item.text),
                )
            )
        # A new version becomes current only after successful parsing.
        db.execute(
            update(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.logical_name == name,
                ProjectFile.id != file.id,
                ProjectFile.is_current.is_(True),
            )
            .values(is_current=False)
        )
        file.status = "ready"
        file.is_current = True
    except Exception as exc:
        file.status = "error"
        file.error_message = str(exc)[:1000]
        file.is_current = False
    db.commit()
    db.refresh(file)
    return file


@router.get("/{project_id}/files", response_model=list[FileRead])
def list_files(
    project_id: str,
    include_history: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectFile]:
    require_project_role(db, user, project_id, "viewer")
    stmt = select(ProjectFile).where(ProjectFile.project_id == project_id)
    if not include_history:
        stmt = stmt.where(ProjectFile.is_current.is_(True))
    return list(db.scalars(stmt.order_by(ProjectFile.logical_name, ProjectFile.version.desc())).all())


@router.get("/{project_id}/files/{file_id}", response_model=FileRead)
def get_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectFile:
    require_project_role(db, user, project_id, "viewer")
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    return file


@router.get("/{project_id}/files/{file_id}/content")
def download_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    require_project_role(db, user, project_id, "viewer")
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(file.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="File content is unavailable")
    return Response(
        content=path.read_bytes(),
        media_type=file.media_type,
        headers={"Content-Disposition": f'attachment; filename="{file.original_name}"'},
    )


@router.get("/{project_id}/file-search", response_model=list[FileChunkRead])
def search_files(
    project_id: str,
    q: str = Query(min_length=2, max_length=1000),
    limit: int = Query(default=6, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FileChunkRead]:
    require_project_role(db, user, project_id, "viewer")
    rows = retrieve_chunks(db, project_id, q, limit=limit)
    return [
        FileChunkRead(id=chunk.id, ordinal=chunk.ordinal, page_number=chunk.page_number, content=chunk.content, score=score)
        for chunk, _file, score in rows
    ]


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    require_project_role(db, user, project_id, "manager")
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(file.storage_path)
    db.delete(file)
    db.commit()
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        # DB state is authoritative; orphan cleanup is safe to retry later.
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
