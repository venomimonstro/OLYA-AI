from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

TOKEN = os.environ.get("X1_DOCUMENT_RENDER_WORKER_TOKEN", "")
DATA_ROOT = Path(os.environ.get("X1_DATA_ROOT", "/app/data")).resolve()
DOCUMENTS_ROOT = (DATA_ROOT / "documents").resolve()
MAX_CONCURRENT = max(1, int(os.environ.get("X1_DOCUMENT_MAX_CONCURRENT_RENDERS", "1")))
MAX_PAGES = max(1, int(os.environ.get("X1_DOCUMENT_MAX_PAGES", "300")))
MAX_DPI = max(72, int(os.environ.get("X1_DOCUMENT_MAX_RASTER_DPI", "150")))
_GATE = threading.BoundedSemaphore(MAX_CONCURRENT)


class RenderRequest(BaseModel):
    docx_rel: str = Field(min_length=1, max_length=600)
    output_dir_rel: str = Field(min_length=1, max_length=600)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    max_pages: int = Field(default=300, ge=1, le=1000)
    raster_dpi: int = Field(default=110, ge=72, le=200)


def _auth(value: str) -> None:
    if not TOKEN or TOKEN == "change-me-document-worker" or not secrets.compare_digest(value, TOKEN):
        raise HTTPException(status_code=403, detail="Document worker authentication failed")


def _safe_data_path(value: str, *, require_documents: bool = True) -> Path:
    rel = Path(value)
    if rel.is_absolute() or not value or any(part in {"", ".", ".."} for part in rel.parts):
        raise HTTPException(status_code=422, detail="Invalid document worker path")
    if require_documents and (not rel.parts or rel.parts[0] != "documents"):
        raise HTTPException(status_code=422, detail="Document worker path must be inside documents")
    resolved = (DATA_ROOT / rel).resolve()
    approved_root = DOCUMENTS_ROOT if require_documents else DATA_ROOT
    try:
        resolved.relative_to(approved_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Document worker path escaped approved data root") from exc
    return resolved


def _binary(*names: str) -> str:
    for name in names:
        value = shutil.which(name)
        if value:
            return value
    raise HTTPException(status_code=503, detail=f"Required renderer binary is unavailable: {names[0]}")


def _run(argv: list[str], *, timeout: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Document render timed out") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Document render process could not start") from exc


app = FastAPI(title="X1 Document Render Worker", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "stable" if (shutil.which("libreoffice") or shutil.which("soffice")) and shutil.which("pdftoppm") else "degraded",
        "max_concurrent": MAX_CONCURRENT,
        "max_pages": MAX_PAGES,
        "max_raster_dpi": MAX_DPI,
    }


@app.post("/render")
def render(payload: RenderRequest, x_x1_document_token: str = Header(default="", alias="X-X1-Document-Token")) -> dict[str, Any]:
    _auth(x_x1_document_token)
    if payload.max_pages > MAX_PAGES or payload.raster_dpi > MAX_DPI:
        raise HTTPException(status_code=422, detail="Document render request exceeds worker limits")
    if not _GATE.acquire(timeout=2.0):
        raise HTTPException(status_code=429, detail="Document renderer is busy", headers={"Retry-After": "3"})
    try:
        docx = _safe_data_path(payload.docx_rel)
        output_dir = _safe_data_path(payload.output_dir_rel)
        if not docx.is_file() or docx.suffix.lower() != ".docx":
            raise HTTPException(status_code=404, detail="DOCX source not found")
        output_dir.mkdir(parents=True, exist_ok=True)
        pages_dir = output_dir / "pages"
        if pages_dir.exists():
            if pages_dir.is_symlink():
                raise HTTPException(status_code=422, detail="Document pages directory cannot be a symlink")
            shutil.rmtree(pages_dir, ignore_errors=True)
        pages_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="x1-doc-worker-") as profile:
            env = {**os.environ, "HOME": profile}
            libreoffice = _binary("libreoffice", "soffice")
            cmd = [
                libreoffice,
                "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", "pdf", "--outdir", str(output_dir), str(docx),
            ]
            result = _run(cmd, timeout=payload.timeout_seconds, env=env)
        pdf = output_dir / f"{docx.stem}.pdf"
        if result.returncode != 0 or not pdf.is_file() or pdf.stat().st_size < 500:
            detail = (result.stderr or result.stdout or "LibreOffice conversion failed")[-1500:]
            raise HTTPException(status_code=422, detail=detail)

        raster = _run(
            [_binary("pdftoppm"), "-png", "-r", str(payload.raster_dpi), str(pdf), str(pages_dir / "page")],
            timeout=max(payload.timeout_seconds, 90),
        )
        pngs = sorted(pages_dir.glob("page-*.png"))
        if raster.returncode != 0 or not pngs:
            detail = (raster.stderr or raster.stdout or "PDF rasterization failed")[-1500:]
            raise HTTPException(status_code=422, detail=detail)
        if len(pngs) > payload.max_pages:
            raise HTTPException(status_code=422, detail="Rendered document exceeds QA page limit")

        return {
            "status": "rendered",
            "pdf_rel": pdf.relative_to(DATA_ROOT).as_posix(),
            "pages_dir_rel": pages_dir.relative_to(DATA_ROOT).as_posix(),
            "page_count": len(pngs),
            "raster_dpi": payload.raster_dpi,
            "renderer": "libreoffice+pdftoppm",
        }
    finally:
        _GATE.release()
