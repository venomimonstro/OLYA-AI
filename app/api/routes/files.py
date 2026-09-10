from __future__ import annotations

import asyncio
import mimetypes
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FileChunk, Project, ProjectFile, User
from app.schemas.files import FileChunkRead, FileRead
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.file_parse_isolation import FileParseBusyError, FileParseError, parse_file_isolated
from app.services.files import chunk_segments, next_file_version, recover_stale_processing, retrieve_chunks, safe_filename, sha256_bytes, storage_path

router = APIRouter(prefix="/v1/projects", tags=["files"])


async def _read_limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size > limit:
            raise HTTPException(status_code=413, detail="File is too large")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="File is too large")
        data.extend(chunk)
    return bytes(data)


def _download_disposition(filename: str) -> str:
    safe = safe_filename(filename)
    fallback = safe.encode("ascii", errors="ignore").decode("ascii").replace('"', "_") or "file"
    encoded = quote(safe, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _ensure_storage_capacity(db: Session, user: User, root: Path, incoming_bytes: int, settings) -> None:
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    minimum_bytes = max(0, int(settings.file_storage_min_free_bytes))
    minimum_percent = max(0.0, min(99.0, float(settings.file_storage_min_free_percent)))
    required_free = max(minimum_bytes, int(usage.total * minimum_percent / 100.0))
    if usage.free - max(0, int(incoming_bytes)) < required_free:
        raise HTTPException(status_code=507, detail="File storage does not have enough safe free space for this upload")
    used = int(db.scalar(select(func.coalesce(func.sum(ProjectFile.size_bytes), 0)).where(ProjectFile.uploaded_by == user.id)) or 0)
    quota = max(0, int(settings.file_user_storage_quota_bytes))
    if quota and used + incoming_bytes > quota:
        raise HTTPException(status_code=507, detail="User file storage quota reached")


async def _process_stored_file(db: Session, file: ProjectFile, settings) -> ProjectFile:
    destination = Path(file.storage_path)
    if not destination.is_file():
        file.status = "error"
        file.error_message = "Stored file content is unavailable; upload a new version"
        file.is_current = False
        db.commit()
        db.refresh(file)
        return file
    try:
        segments = await asyncio.to_thread(
            parse_file_isolated,
            destination,
            file.original_name,
            max_pdf_pages=int(settings.max_pdf_pages),
            max_docx_unpacked_bytes=int(settings.max_docx_unpacked_bytes),
            max_extracted_chars=int(settings.file_max_extracted_chars),
            timeout_seconds=int(settings.file_parse_timeout_seconds),
            memory_mb=int(settings.file_parse_memory_mb),
            queue_timeout_seconds=float(settings.file_parse_queue_timeout_seconds),
        )
    except FileParseBusyError as exc:
        row = db.get(ProjectFile, file.id)
        if row is not None:
            row.status = "error"
            row.error_message = "File parser is at safe capacity; retry shortly"
            row.is_current = False
            db.commit()
        raise HTTPException(
            status_code=503,
            detail="File parser is at safe capacity; retry shortly",
            headers={"Retry-After": "3"},
        ) from exc
    except FileParseError as exc:
        row = db.get(ProjectFile, file.id)
        if row is not None:
            row.status = "error"
            row.error_message = str(exc)[:1000]
            row.is_current = False
            db.commit()
            db.refresh(row)
            return row
        raise HTTPException(status_code=422, detail="File parsing failed") from exc

    chunks = chunk_segments(
        segments,
        max_chars=settings.file_chunk_chars,
        overlap_chars=settings.file_chunk_overlap_chars,
    )
    db.execute(select(Project.id).where(Project.id == file.project_id).with_for_update())
    row = db.get(ProjectFile, file.id)
    if row is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="File state disappeared during parsing")
    db.execute(delete(FileChunk).where(FileChunk.file_id == row.id))
    if not chunks:
        row.status = "error"
        row.error_message = "No readable text found"
        row.is_current = False
        db.commit()
        db.refresh(row)
        return row
    for ordinal, item in enumerate(chunks):
        db.add(FileChunk(file_id=row.id, ordinal=ordinal, page_number=item.page_number, content=item.text, content_sha256=sha256_bytes(item.text.encode("utf-8")), char_count=len(item.text)))
    newer_current = db.scalar(select(ProjectFile.id).where(ProjectFile.project_id == row.project_id, ProjectFile.logical_name == row.logical_name, ProjectFile.version > row.version, ProjectFile.is_current.is_(True)).limit(1))
    if newer_current is None:
        db.execute(update(ProjectFile).where(ProjectFile.project_id == row.project_id, ProjectFile.logical_name == row.logical_name, ProjectFile.id != row.id, ProjectFile.is_current.is_(True)).values(is_current=False))
        row.is_current = True
    else:
        row.is_current = False
    row.status = "ready"
    row.error_message = ""
    db.commit()
    db.refresh(row)
    return row


@router.post("/{project_id}/files", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(project_id: str, request: Request, filename: str = Query(min_length=1, max_length=240), logical_name: str | None = Query(default=None, max_length=240), user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectFile:
    _project, role = require_project_role(db, user, project_id, "member")
    settings = request.app.state.settings
    db.commit()
    content = await _read_limited_body(request, int(settings.max_file_size_bytes))
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    name = safe_filename(logical_name or filename)
    digest = sha256_bytes(content)
    root = Path(settings.file_storage_path).resolve()
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    current_same_name = db.scalar(select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.logical_name == name, ProjectFile.is_current.is_(True)))
    if current_same_name is not None and role not in {"owner", "manager"}:
        db.rollback()
        raise HTTPException(status_code=403, detail="Only project manager can replace an existing file")
    existing = db.scalar(select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.logical_name == name, ProjectFile.content_sha256 == digest, ProjectFile.is_current.is_(True)))
    if existing is not None:
        db.commit()
        return existing
    _ensure_storage_capacity(db, user, root, len(content), settings)
    version = next_file_version(db, project_id, name)
    file = ProjectFile(project_id=project_id, uploaded_by=user.id, logical_name=name, original_name=safe_filename(filename), version=version, content_sha256=digest, media_type=request.headers.get("content-type") or mimetypes.guess_type(filename)[0] or "application/octet-stream", size_bytes=len(content), storage_path="", status="processing")
    db.add(file)
    db.flush()
    destination = storage_path(root, project_id, file.id, version, filename)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    except OSError as exc:
        db.rollback()
        try:
            if destination.is_file(): destination.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=507, detail="File storage is temporarily unavailable") from exc
    file.storage_path = str(destination)
    db.commit()
    return await _process_stored_file(db, file, settings)


@router.get("/{project_id}/files", response_model=list[FileRead])
def list_files(project_id: str, request: Request, include_history: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ProjectFile]:
    require_project_role(db, user, project_id, "viewer")
    timeout = max(900, int(request.app.state.settings.file_parse_timeout_seconds) + 300)
    if recover_stale_processing(db, project_id=project_id, timeout_seconds=timeout):
        db.commit()
    stmt = select(ProjectFile).where(ProjectFile.project_id == project_id)
    if not include_history:
        # Failed/interrupted uploads must remain visible even though they are
        # intentionally excluded from the current RAG version.
        stmt = stmt.where(or_(ProjectFile.is_current.is_(True), ProjectFile.status != "ready"))
    return list(db.scalars(stmt.order_by(ProjectFile.logical_name, ProjectFile.version.desc())).all())


@router.post("/{project_id}/files/{file_id}/retry", response_model=FileRead)
async def retry_file(project_id: str, file_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectFile:
    require_project_role(db, user, project_id, "manager")
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    file = db.scalar(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.project_id == project_id).with_for_update())
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if file.status == "ready":
        db.commit()
        return file
    if file.status == "processing":
        db.rollback()
        raise HTTPException(status_code=409, detail="File is already processing")
    if file.status != "error":
        db.rollback()
        raise HTTPException(status_code=409, detail="Only failed files can be retried")
    file.status = "processing"
    file.error_message = ""
    file.is_current = False
    db.commit()
    return await _process_stored_file(db, file, request.app.state.settings)


@router.post("/{project_id}/files/{file_id}/make-current", response_model=FileRead)
def make_file_current(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectFile:
    require_project_role(db, user, project_id, "manager")
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    if file.status != "ready":
        db.rollback()
        raise HTTPException(status_code=409, detail="Only a ready file version can be current")
    db.execute(update(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.logical_name == file.logical_name, ProjectFile.id != file.id, ProjectFile.is_current.is_(True)).values(is_current=False))
    file.is_current = True
    db.commit()
    db.refresh(file)
    return file


@router.get("/{project_id}/files/{file_id}", response_model=FileRead)
def get_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProjectFile:
    require_project_role(db, user, project_id, "viewer")
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id: raise HTTPException(status_code=404, detail="File not found")
    return file


@router.get("/{project_id}/files/{file_id}/content")
def download_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    require_project_role(db, user, project_id, "viewer")
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id: raise HTTPException(status_code=404, detail="File not found")
    path = Path(file.storage_path)
    if not path.is_file(): raise HTTPException(status_code=410, detail="File content is unavailable")
    return FileResponse(path=path, media_type=file.media_type, headers={"Content-Disposition": _download_disposition(file.original_name)})


@router.get("/{project_id}/file-search", response_model=list[FileChunkRead])
def search_files(project_id: str, q: str = Query(min_length=2, max_length=1000), limit: int = Query(default=6, ge=1, le=20), user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[FileChunkRead]:
    require_project_role(db, user, project_id, "viewer")
    rows = retrieve_chunks(db, project_id, q, limit=limit)
    return [FileChunkRead(id=chunk.id, ordinal=chunk.ordinal, page_number=chunk.page_number, content=chunk.content, score=score) for chunk, _file, score in rows]


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(project_id: str, file_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    require_project_role(db, user, project_id, "manager")
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    file = db.get(ProjectFile, file_id)
    if file is None or file.project_id != project_id: raise HTTPException(status_code=404, detail="File not found")
    path = Path(file.storage_path)
    logical_name = file.logical_name
    was_current = bool(file.is_current)
    db.delete(file)
    db.flush()
    if was_current:
        replacement = db.scalar(
            select(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.logical_name == logical_name,
                ProjectFile.status == "ready",
            )
            .order_by(ProjectFile.version.desc())
            .limit(1)
        )
        if replacement is not None:
            replacement.is_current = True
    db.commit()
    try:
        if path.is_file(): path.unlink()
    except OSError:
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
