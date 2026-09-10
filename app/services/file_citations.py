from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileChunk, ProjectFile
from app.schemas.files import FileCitationRead


_FILE_REF = re.compile(
    r"FILE_REF\[file_id=([^;\]\s]+);name=.*?;version=(\d+);chunk=(\d+)(?:;page=(\d+))?\]"
)


def citations_from_answer(
    db: Session,
    project_id: str | None,
    answer: str,
    evidence_context: str,
    *,
    limit: int = 20,
    excerpt_chars: int = 700,
) -> list[FileCitationRead]:
    """Resolve model-emitted FILE_REF values against authorized project data."""
    if not project_id or not answer or not evidence_context:
        return []
    allowed = {(match.group(1), int(match.group(3))) for match in _FILE_REF.finditer(evidence_context)}
    if not allowed:
        return []
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in _FILE_REF.finditer(answer):
        key = (match.group(1), int(match.group(3)))
        if key in allowed and key not in seen:
            refs.append(key)
            seen.add(key)
        if len(refs) >= max(1, min(int(limit), 20)):
            break
    if not refs:
        return []

    file_ids = {file_id for file_id, _ordinal in refs}
    rows = db.execute(
        select(FileChunk, ProjectFile)
        .join(ProjectFile, ProjectFile.id == FileChunk.file_id)
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.status == "ready",
            ProjectFile.id.in_(file_ids),
        )
    ).all()
    indexed = {(file.id, chunk.ordinal): (chunk, file) for chunk, file in rows}
    result: list[FileCitationRead] = []
    bound = max(120, min(int(excerpt_chars), 1000))
    for key in refs:
        resolved = indexed.get(key)
        if resolved is None:
            continue
        chunk, file = resolved
        excerpt = " ".join(str(chunk.content or "").split())[:bound]
        result.append(
            FileCitationRead(
                file_id=file.id,
                logical_name=file.logical_name,
                version=file.version,
                chunk=chunk.ordinal,
                page_number=chunk.page_number,
                excerpt=excerpt,
            )
        )
    return result
