from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DocumentArtifact, DocumentQAEvent, DocumentRevision, User
from app.schemas.documents import DocumentArtifactRead, DocumentRevisionRead, DocumentSpec
from app.services.access import ROLE_RANK, require_project_role
from app.services.auth import get_current_user
from app.services.documents import (
    DocumentBuildError,
    DocumentBusyError,
    DocumentQAError,
    build_docx,
    render_document_artifacts,
    render_qa,
    repair_docx_layout,
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
        _project, role = require_project_role(db, user, artifact.project_id, "member")
        if release and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Project document release requires manager role")
        if not release and artifact.user_id != user.id and ROLE_RANK.get(role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Only the document creator or project manager can modify it")
    elif artifact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    locked = db.scalar(select(DocumentArtifact).where(DocumentArtifact.id == artifact.id).with_for_update())
    if locked is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return locked


def _revision(db: Session, artifact: DocumentArtifact, revision: int | None = None) -> DocumentRevision:
    rev_number = revision or artifact.current_revision
    row = db.scalar(select(DocumentRevision).where(DocumentRevision.artifact_id == artifact.id, DocumentRevision.revision == rev_number))
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


def _finish_qa(
    db: Session,
    user: User,
    *,
    artifact_id: str,
    revision_number: int,
    source_docx_sha: str,
    report: dict,
    final_docx_sha: str | None = None,
    pdf_path: Path | None = None,
    pdf_sha: str = "",
    page_count: int = 0,
) -> DocumentRevision:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    artifact = _artifact_write_access(db, user, artifact)
    if artifact.current_revision != revision_number:
        db.rollback()
        raise HTTPException(status_code=409, detail="Document advanced to a newer revision while QA was running")
    revision = _revision(db, artifact, revision_number)
    if revision.docx_sha256 != source_docx_sha:
        db.rollback()
        raise HTTPException(status_code=409, detail="Document changed while QA was running")
    if final_docx_sha:
        revision.docx_sha256 = final_docx_sha
    if pdf_path is not None and pdf_sha:
        revision.pdf_path = str(pdf_path)
        revision.pdf_sha256 = pdf_sha
        revision.page_count = int(page_count)
    revision.qa_status = "passed" if report.get("status") == "passed" else "failed"
    revision.qa_report = report
    artifact.status = "qa_passed" if revision.qa_status == "passed" else "qa_failed"
    for gate in report.get("events") or []:
        _record_gate(db, revision, str(gate.get("gate") or "qa"), dict(gate.get("report") or {}))
    db.commit()
    db.refresh(revision)
    return revision


def _reset_busy_qa(
    db: Session,
    user: User,
    *,
    artifact_id: str,
    revision_number: int,
    source_docx_sha: str,
    repaired_docx_sha: str | None = None,
) -> None:
    try:
        artifact = _artifact_access(db, user, artifact_id, "viewer")
        artifact = _artifact_write_access(db, user, artifact)
        if artifact.current_revision != revision_number:
            db.rollback(); return
        revision = _revision(db, artifact, revision_number)
        if revision.docx_sha256 != source_docx_sha:
            db.rollback(); return
        if repaired_docx_sha and repaired_docx_sha != source_docx_sha:
            revision.docx_sha256 = repaired_docx_sha
        if revision.qa_status == "running":
            revision.qa_status = "pending"
            artifact.status = "draft"
        db.commit()
    except Exception:
        db.rollback()


@router.post("", response_model=DocumentArtifactRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentSpec, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentArtifact:
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    artifact = DocumentArtifact(user_id=user.id, project_id=payload.project_id, title=payload.title.strip(), logical_name=safe_doc_name(payload.logical_name), status="draft", current_revision=1)
    db.add(artifact); db.flush()
    revision = DocumentRevision(artifact_id=artifact.id, revision=1, spec=payload.model_dump(mode="json"), created_by=user.id)
    db.add(revision); db.flush()
    try:
        _create_revision_files(request, artifact, revision, revision.spec)
    except (DocumentBuildError, OSError) as exc:
        db.rollback()
        import shutil
        shutil.rmtree(Path(request.app.state.settings.document_storage_path).resolve() / artifact.id, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Document generation failed") from exc
    db.commit(); db.refresh(artifact)
    return artifact


@router.get("", response_model=list[DocumentArtifactRead])
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DocumentArtifact]:
    personal = list(db.scalars(select(DocumentArtifact).where(DocumentArtifact.user_id == user.id)).all())
    seen = {item.id for item in personal}
    from app.services.access import list_accessible_projects
    project_ids = [p.id for p in list_accessible_projects(db, user.id)]
    shared = list(db.scalars(select(DocumentArtifact).where(DocumentArtifact.project_id.in_(project_ids))).all()) if project_ids else []
    return sorted(personal + [item for item in shared if item.id not in seen], key=lambda x: x.updated_at, reverse=True)


@router.get("/{artifact_id}", response_model=DocumentArtifactRead)
def get_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentArtifact:
    return _artifact_access(db, user, artifact_id, "viewer")


@router.get("/{artifact_id}/revisions", response_model=list[DocumentRevisionRead])
def list_revisions(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DocumentRevision]:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    return list(db.scalars(select(DocumentRevision).where(DocumentRevision.artifact_id == artifact.id).order_by(DocumentRevision.revision.desc())).all())


@router.post("/{artifact_id}/revisions", response_model=DocumentRevisionRead, status_code=status.HTTP_201_CREATED)
def revise_document(artifact_id: str, payload: DocumentSpec, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentRevision:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    artifact = _artifact_write_access(db, user, artifact)
    if payload.project_id and payload.project_id != artifact.project_id:
        raise HTTPException(status_code=400, detail="Document project cannot be changed by revision")
    next_revision = artifact.current_revision + 1
    spec = payload.model_dump(mode="json"); spec["project_id"] = artifact.project_id
    revision = DocumentRevision(artifact_id=artifact.id, revision=next_revision, spec=spec, created_by=user.id)
    db.add(revision); db.flush()
    try:
        _create_revision_files(request, artifact, revision, spec)
    except (DocumentBuildError, OSError) as exc:
        db.rollback()
        import shutil
        shutil.rmtree(Path(request.app.state.settings.document_storage_path).resolve() / artifact.id / f"r{next_revision}", ignore_errors=True)
        raise HTTPException(status_code=422, detail="Document generation failed") from exc
    artifact.title = payload.title.strip(); artifact.logical_name = safe_doc_name(payload.logical_name); artifact.current_revision = next_revision; artifact.status = "draft"
    db.commit(); db.refresh(revision)
    return revision


@router.post("/{artifact_id}/qa", response_model=DocumentRevisionRead)
def run_document_qa(artifact_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentRevision:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    artifact = _artifact_write_access(db, user, artifact)
    revision = _revision(db, artifact)
    if revision.qa_status == "running":
        db.rollback()
        raise HTTPException(status_code=409, detail="Document QA is already running for this revision")
    docx = Path(revision.docx_path)
    if not docx.is_file() or sha256_file(docx) != revision.docx_sha256:
        report = {"status": "failed", "issues": [{"code": "docx_missing_or_changed"}], "events": []}
        revision.qa_status = "failed"; revision.qa_report = report; artifact.status = "qa_failed"
        _record_gate(db, revision, "integrity", report)
        db.commit(); db.refresh(revision)
        return revision
    revision_number = int(revision.revision)
    revision_id = revision.id
    source_docx_sha = revision.docx_sha256
    spec = dict(revision.spec or {})
    revision.qa_status = "running"; artifact.status = "qa_running"
    _record_gate(db, revision, "qa_started", {"status": "running", "source_docx_sha256": source_docx_sha})
    db.commit()

    events: list[dict] = []
    structural = structural_qa(docx, spec)
    events.append({"gate": "structural", "report": structural})
    if structural["status"] != "passed":
        report = {"status": "failed", "structural": structural, "repairs": [], "events": events}
        return _finish_qa(db, user, artifact_id=artifact_id, revision_number=revision_number, source_docx_sha=source_docx_sha, report=report)

    settings = request.app.state.settings
    root = Path(settings.document_storage_path).resolve()
    rev_dir = root / artifact_id / f"r{revision_number}"
    data_root = root.parent if root.name == "documents" else Path("./data").resolve()
    repairs: list[dict] = []
    pdf: Path | None = None
    rendered: dict = {"status": "failed", "issues": [{"code": "render_not_started"}]}
    render_meta: dict = {}
    max_repairs = max(0, min(int(getattr(settings, "document_qa_max_repairs", 1)), 2))
    try:
        for attempt in range(max_repairs + 1):
            pdf, pages_dir, render_meta = render_document_artifacts(
                docx,
                rev_dir,
                backend=settings.document_render_backend,
                worker_url=settings.document_render_worker_url,
                worker_token=settings.document_render_worker_token,
                data_root=data_root,
                timeout_seconds=settings.document_render_timeout_seconds,
                max_pages=settings.document_max_pages,
                raster_dpi=settings.document_raster_dpi,
            )
            rendered = render_qa(pdf, pages_dir, max_pages=settings.document_max_pages)
            rendered["render_meta"] = render_meta
            events.append({"gate": f"render_attempt_{attempt + 1}", "report": rendered})
            if rendered["status"] == "passed":
                break
            if attempt >= max_repairs:
                break
            repair = repair_docx_layout(docx, rendered.get("issues") or [])
            repair["attempt"] = attempt + 1
            repairs.append(repair)
            events.append({"gate": f"repair_{attempt + 1}", "report": {"status": "passed" if repair.get("applied") else "failed", **repair}})
            if not repair.get("applied"):
                break
    except DocumentBusyError as exc:
        repaired_sha = sha256_file(docx) if docx.is_file() else None
        _reset_busy_qa(db, user, artifact_id=artifact_id, revision_number=revision_number, source_docx_sha=source_docx_sha, repaired_docx_sha=repaired_sha)
        raise HTTPException(status_code=503, detail="Document renderer is busy; retry shortly", headers={"Retry-After": "3"}) from exc
    except DocumentQAError as exc:
        rendered = {"status": "failed", "issues": [{"code": "render_failed", "message": str(exc)}]}
        events.append({"gate": "render", "report": rendered})

    final_docx_sha = sha256_file(docx) if docx.is_file() else ""
    pdf_sha = sha256_file(pdf) if pdf is not None and pdf.is_file() else ""
    report = {
        "status": "passed" if rendered.get("status") == "passed" and bool(pdf_sha) else "failed",
        "structural": structural,
        "render": rendered,
        "repairs": repairs,
        "source_docx_sha256": source_docx_sha,
        "final_docx_sha256": final_docx_sha,
        "revision_id": revision_id,
        "events": events,
    }
    return _finish_qa(
        db,
        user,
        artifact_id=artifact_id,
        revision_number=revision_number,
        source_docx_sha=source_docx_sha,
        report=report,
        final_docx_sha=final_docx_sha or None,
        pdf_path=pdf if pdf_sha else None,
        pdf_sha=pdf_sha,
        page_count=int(rendered.get("page_count") or 0),
    )


@router.post("/{artifact_id}/release", response_model=DocumentArtifactRead)
def release_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DocumentArtifact:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    artifact = _artifact_write_access(db, user, artifact, release=True)
    revision = _revision(db, artifact)
    if revision.qa_status != "passed" or not revision.pdf_sha256:
        raise HTTPException(status_code=409, detail="Document cannot be released before successful final QA")
    if not Path(revision.docx_path).is_file() or sha256_file(Path(revision.docx_path)) != revision.docx_sha256:
        raise HTTPException(status_code=409, detail="Document changed after QA")
    if not Path(revision.pdf_path).is_file() or sha256_file(Path(revision.pdf_path)) != revision.pdf_sha256:
        raise HTTPException(status_code=409, detail="Rendered document changed after QA")
    artifact.released_revision = revision.revision; artifact.status = "released"
    db.commit(); db.refresh(artifact)
    return artifact


@router.get("/{artifact_id}/download")
def download_released_document(artifact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    artifact = _artifact_access(db, user, artifact_id, "viewer")
    if artifact.released_revision is None:
        raise HTTPException(status_code=409, detail="Document has not passed final QA and release")
    revision = _revision(db, artifact, artifact.released_revision)
    path = Path(revision.docx_path)
    if not path.is_file() or sha256_file(path) != revision.docx_sha256:
        raise HTTPException(status_code=410, detail="Released document is unavailable or failed integrity check")
    return FileResponse(path=path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=safe_doc_name(artifact.logical_name))
