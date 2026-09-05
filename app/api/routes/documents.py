from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DocumentArtifact, DocumentQAEvent, DocumentRevision, User
from app.schemas.documents import DocumentArtifactRead, DocumentRevisionRead, DocumentSpec
from app.services.access import ROLE_RANK, project_role, require_project_role
from app.services.auth import get_current_user
from app.services.documents import (
    DocumentBuildError,
    DocumentQAError,
    build_docx,
    render_docx_to_pdf,
    render_qa,
    safe_doc_name,
    sha256_file,
    structural_qa,
)

router = APIRouter(prefix="/v1/documents", tags=["documents"])


def _artifact_access(db: Session, user: User, artifact_id: str, minimum: str = "viewer") -> DocumentArtifact:
    artifact = db.get(DocumentArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if artifact.project_id:
        require_project_role(db, user, artifact.project_id, minimum)
        return artifact
    if artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return artifact



def _artifact_write_access(db: Session, user: User, artifact: DocumentArtifact, *, release: bool = False) -> DocumentArtifact:
    if artifact.project_id:
        project, role = require_project_role(db, user, artifact.project_id, "member")
        _ = project
        if release and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Project document release requires manager role")
        if not release and artifact.user_id != user.id and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Only the document creator or project manager can modify it")
        return artifact
    if artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return artifact

def _revision(db: Session, artifact: DocumentArtifact, revision: int | None = None) -> DocumentRevision:
    rev_number = revision or artifact.current_revision
    row = db.scalar(
        select(DocumentRevision).where(
            DocumentRevision.artifact_id == artifact.id,
            DocumentRevision.revision == rev_number,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Document revision not found")
    return row


def _record_gate(db: Session, revision: DocumentRevision, gate: str, report: dict) -> None:
    db.add(DocumentQAEvent(revision_id=revision.id, gate=gate, status=report.get("status", "failed"), details=report))


def _create_revision_files(request: Request, artifact: DocumentArtifact, revision: DocumentRevision, spec: dict) -> None:
    root = Path(request.app.state.settings.document_storage_path).resolve()
    rev_dir = root / artifact.id / f"r{revision.revision}"
    docx = rev_dir / safe_doc_name(artifact.logical_name)
    build_docx(spec, docx)
    revision.docx_path = str(docx)
    revision.docx_sha256 = sha256_file(docx)


@router.post("", response_model=DocumentArtifactRead, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentSpec,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentArtifact:
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    artifact = DocumentArtifact(
        user_id=user.id,
        project_id=payload.project_id,
        title=payload.title.strip(),
        logical_name=safe_doc_name(payload.logical_name),
        status="draft",
        current_revision=1,
    )
    db.add(artifact)
    db.flush()
    revision = DocumentRevision(
        artifact_id=artifact.id,
        revision=1,
        spec=payload.model_dump(mode="json"),
        created_by=user.id,
    )
    db.add(revision)
    db.flush()
    try:
        _create_revision_files(request, artifact, revision, revision.spec)
    except (DocumentBuildError, OSError) as exc:
        db.rollback()
        root = Path(request.app.state.settings.document_storage_path).resolve()
        import shutil
        shutil.rmtree(root / artifact.id, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Document generation failed") from exc
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("", response_model=list[DocumentArtifactRead])
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DocumentArtifact]:
    # Personal documents plus project documents are deliberately fetched conservatively.
    personal = list(db.scalars(select(DocumentArtifact).where(DocumentArtifact.user_id == user.id)).all())
    seen = {item.id for item in personal}
    from app.services.access import list_accessible_projects
    project_ids = [p.id for p in list_accessible_projects(db, user.id)]
    shared = []
    if project_ids:
        shared = list(db.scalars(select(DocumentArtifact).where(DocumentArtifact.project_id.in_(project_ids))).all())
    rows = personal + [item for item in shared if item.id not in seen]
    return sorted(rows, key=lambda x: x.updated_at, reverse=True)


@router.get("/{artifact_id}", response_model=DocumentArtifactRead)
def get_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentArtifact:
    return _artifact_access(db, user, artifact_id, "viewer")


@router.get("/{artifact_id}/revisions", response_model=list[DocumentRevisionRead])
def list_revisions(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DocumentRevision]:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    return list(db.scalars(select(DocumentRevision).where(DocumentRevision.artifact_id == artifact.id).order_by(DocumentRevision.revision.desc())).all())


@router.post("/{artifact_id}/revisions", response_model=DocumentRevisionRead, status_code=status.HTTP_201_CREATED)
def revise_document(
    artifact_id: str,
    payload: DocumentSpec,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentRevision:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    _artifact_write_access(db, user, artifact)
    if payload.project_id and payload.project_id != artifact.project_id:
        raise HTTPException(status_code=400, detail="Document project cannot be changed by revision")
    next_revision = artifact.current_revision + 1
    spec = payload.model_dump(mode="json")
    spec["project_id"] = artifact.project_id
    revision = DocumentRevision(artifact_id=artifact.id, revision=next_revision, spec=spec, created_by=user.id)
    db.add(revision)
    db.flush()
    try:
        _create_revision_files(request, artifact, revision, spec)
    except (DocumentBuildError, OSError) as exc:
        db.rollback()
        root = Path(request.app.state.settings.document_storage_path).resolve()
        import shutil
        shutil.rmtree(root / artifact.id / f"r{next_revision}", ignore_errors=True)
        raise HTTPException(status_code=422, detail="Document generation failed") from exc
    artifact.title = payload.title.strip()
    artifact.logical_name = safe_doc_name(payload.logical_name)
    artifact.current_revision = next_revision
    artifact.status = "draft"
    db.commit()
    db.refresh(revision)
    return revision


@router.post("/{artifact_id}/qa", response_model=DocumentRevisionRead)
def run_document_qa(
    artifact_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentRevision:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    _artifact_write_access(db, user, artifact)
    revision = _revision(db, artifact)
    docx = Path(revision.docx_path)
    if not docx.is_file() or sha256_file(docx) != revision.docx_sha256:
        report = {"status": "failed", "issues": [{"code": "docx_missing_or_changed"}]}
        revision.qa_status = "failed"
        revision.qa_report = report
        artifact.status = "qa_failed"
        _record_gate(db, revision, "integrity", report)
        db.commit()
        return revision

    structural = structural_qa(docx, revision.spec)
    _record_gate(db, revision, "structural", structural)
    if structural["status"] != "passed":
        revision.qa_status = "failed"
        revision.qa_report = {"structural": structural}
        artifact.status = "qa_failed"
        db.commit()
        return revision

    try:
        root = Path(request.app.state.settings.document_storage_path).resolve()
        rev_dir = root / artifact.id / f"r{revision.revision}"
        pdf = render_docx_to_pdf(docx, rev_dir, timeout_seconds=request.app.state.settings.document_render_timeout_seconds)
        rendered = render_qa(pdf, rev_dir / "pages", max_pages=request.app.state.settings.document_max_pages)
    except DocumentQAError as exc:
        rendered = {"status": "failed", "issues": [{"code": "render_failed", "message": str(exc)}]}
        _record_gate(db, revision, "render", rendered)
        revision.qa_status = "failed"
        revision.qa_report = {"structural": structural, "render": rendered}
        artifact.status = "qa_failed"
        db.commit()
        return revision

    _record_gate(db, revision, "render", rendered)
    revision.pdf_path = str(pdf)
    revision.pdf_sha256 = sha256_file(pdf)
    revision.page_count = int(rendered["page_count"])
    revision.qa_status = "passed" if rendered["status"] == "passed" else "failed"
    revision.qa_report = {"structural": structural, "render": rendered}
    artifact.status = "qa_passed" if revision.qa_status == "passed" else "qa_failed"
    db.commit()
    db.refresh(revision)
    return revision


@router.post("/{artifact_id}/release", response_model=DocumentArtifactRead)
def release_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentArtifact:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    _artifact_write_access(db, user, artifact, release=True)
    revision = _revision(db, artifact)
    if revision.qa_status != "passed" or not revision.pdf_sha256:
        raise HTTPException(status_code=409, detail="Document cannot be released before successful final QA")
    if not Path(revision.docx_path).is_file() or sha256_file(Path(revision.docx_path)) != revision.docx_sha256:
        raise HTTPException(status_code=409, detail="Document changed after QA")
    if not Path(revision.pdf_path).is_file() or sha256_file(Path(revision.pdf_path)) != revision.pdf_sha256:
        raise HTTPException(status_code=409, detail="Rendered document changed after QA")
    artifact.released_revision = revision.revision
    artifact.status = "released"
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("/{artifact_id}/download")
def download_released_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    if artifact.released_revision is None:
        raise HTTPException(status_code=409, detail="Document has not passed final QA and release")
    revision = _revision(db, artifact, artifact.released_revision)
    path = Path(revision.docx_path)
    if not path.is_file() or sha256_file(path) != revision.docx_sha256:
        raise HTTPException(status_code=410, detail="Released document is unavailable or failed integrity check")
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_doc_name(artifact.logical_name)}"'},
    )
