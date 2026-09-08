from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from pypdf import PdfReader
from PIL import Image, ImageChops


class DocumentBuildError(RuntimeError):
    pass


class DocumentQAError(RuntimeError):
    pass


class DocumentBusyError(DocumentQAError):
    """Transient renderer capacity condition; never persist as document failure."""


_RENDER_GATE = threading.BoundedSemaphore(1)
_RENDER_WAIT_SECONDS = 5.0


def configure_render_gate(max_concurrent: int = 1, wait_timeout_seconds: float = 5.0) -> None:
    global _RENDER_GATE, _RENDER_WAIT_SECONDS
    _RENDER_GATE = threading.BoundedSemaphore(max(1, int(max_concurrent)))
    _RENDER_WAIT_SECONDS = max(0.1, float(wait_timeout_seconds))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_doc_name(name: str) -> str:
    value = Path(name.replace("\\", "/")).name.strip() or "document.docx"
    if not value.lower().endswith(".docx"):
        value += ".docx"
    return value[:240]


def _repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    marker = OxmlElement("w:tblHeader")
    marker.set(qn("w:val"), "true")
    tr_pr.append(marker)


def build_docx(spec: dict[str, Any], destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(11)
    for level in range(1, 5):
        styles[f"Heading {level}"].font.name = "Arial"
    title = str(spec.get("title") or "").strip()
    if title:
        document.add_heading(title, level=0)
    block_count = 0
    table_count = 0
    for raw in spec.get("blocks") or []:
        kind = raw.get("type")
        if kind == "heading":
            document.add_heading(str(raw.get("text") or ""), level=max(1, min(4, int(raw.get("level") or 1))))
        elif kind == "paragraph":
            document.add_paragraph(str(raw.get("text") or ""))
        elif kind in {"bullet_list", "numbered_list"}:
            style = "List Bullet" if kind == "bullet_list" else "List Number"
            for item in raw.get("items") or []:
                document.add_paragraph(str(item), style=style)
        elif kind == "table":
            rows = raw.get("rows") or []
            if not rows:
                continue
            width = max(len(row) for row in rows)
            if width < 1:
                continue
            table = document.add_table(rows=len(rows), cols=width)
            table.style = "Table Grid"
            table.autofit = True
            for r_idx, row in enumerate(rows):
                for c_idx in range(width):
                    cell = table.cell(r_idx, c_idx)
                    cell.text = str(row[c_idx]) if c_idx < len(row) else ""
                    if r_idx == 0 and bool(raw.get("header", True)):
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            if bool(raw.get("header", True)) and table.rows:
                _repeat_table_header(table.rows[0])
            table_count += 1
        elif kind == "page_break":
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        else:
            raise DocumentBuildError(f"Unsupported document block: {kind}")
        block_count += 1
    document.save(destination)
    if not destination.is_file() or destination.stat().st_size < 1000:
        raise DocumentBuildError("DOCX generation produced an invalid or empty file")
    return {"block_count": block_count, "table_count": table_count, "size_bytes": destination.stat().st_size}


def structural_qa(docx_path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        doc = Document(docx_path)
    except Exception as exc:
        raise DocumentQAError("DOCX cannot be opened") from exc
    expected_tables = [block for block in spec.get("blocks") or [] if block.get("type") == "table" and block.get("rows")]
    if len(doc.tables) != len(expected_tables):
        issues.append({"code": "table_count_mismatch", "expected": len(expected_tables), "actual": len(doc.tables)})
    for index, expected in enumerate(expected_tables):
        if index >= len(doc.tables):
            break
        table = doc.tables[index]
        rows = expected.get("rows") or []
        expected_cols = max((len(row) for row in rows), default=0)
        if len(table.rows) != len(rows):
            issues.append({"code": "table_row_count_mismatch", "table": index + 1, "expected": len(rows), "actual": len(table.rows)})
        if table.rows and len(table.rows[0].cells) != expected_cols:
            issues.append({"code": "table_column_count_mismatch", "table": index + 1, "expected": expected_cols, "actual": len(table.rows[0].cells)})
        longest = max((len(str(cell)) for row in rows for cell in row), default=0)
        if expected_cols >= 7 or longest >= 80:
            warnings.append({"code": "table_layout_risk", "table": index + 1, "columns": expected_cols, "longest_cell_chars": longest})

    paragraph_text = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    table_text = [cell.text.strip() for table in doc.tables for row in table.rows for cell in row.cells if cell.text.strip()]
    all_text = "\n".join(paragraph_text + table_text)
    if not all_text and not doc.tables:
        issues.append({"code": "empty_document"})
    unresolved = [token for token in ("TODO", "TBD", "FIXME", "{{", "}}") if token in all_text]
    if unresolved:
        issues.append({"code": "unresolved_placeholders", "tokens": unresolved})
    return {
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "warnings": warnings,
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
    }


def _office_binary() -> str:
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise DocumentQAError("LibreOffice is unavailable for document rendering")
    return binary


def _acquire_renderer() -> None:
    if not _RENDER_GATE.acquire(timeout=_RENDER_WAIT_SECONDS):
        raise DocumentBusyError("Document renderer is busy; retry shortly")


def _local_render(docx_path: Path, output_dir: Path, *, timeout_seconds: int, raster_dpi: int) -> tuple[Path, Path, dict[str, Any]]:
    _acquire_renderer()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        pages_dir = output_dir / "pages"
        if pages_dir.exists():
            shutil.rmtree(pages_dir, ignore_errors=True)
        pages_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="x1-lo-") as profile:
            cmd = [_office_binary(), "--headless", "--nologo", "--nodefault", "--nofirststartwizard", f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf", "--outdir", str(output_dir), str(docx_path)]
            env = {**os.environ, "HOME": profile}
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, env=env, check=False)
            except subprocess.TimeoutExpired as exc:
                raise DocumentQAError("Document rendering timed out") from exc
        pdf = output_dir / f"{docx_path.stem}.pdf"
        if result.returncode != 0 or not pdf.is_file() or pdf.stat().st_size < 500:
            message = (result.stderr or result.stdout or "LibreOffice conversion failed")[-1000:]
            raise DocumentQAError(message)
        if not shutil.which("pdftoppm"):
            raise DocumentQAError("pdftoppm is unavailable for visual QA")
        raster = subprocess.run(["pdftoppm", "-png", "-r", str(max(72, min(200, int(raster_dpi)))), str(pdf), str(pages_dir / "page")], capture_output=True, text=True, timeout=max(90, timeout_seconds), check=False)
        pngs = sorted(pages_dir.glob("page-*.png"))
        if raster.returncode != 0 or not pngs:
            raise DocumentQAError((raster.stderr or raster.stdout or "Page rasterization failed")[-1000:])
        return pdf, pages_dir, {"renderer": "local-libreoffice+pdftoppm", "page_count": len(pngs), "raster_dpi": raster_dpi}
    finally:
        _RENDER_GATE.release()


def _data_relative(path: Path, data_root: Path) -> str:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(data_root.resolve())
    except ValueError as exc:
        raise DocumentQAError("Document render path is outside data root") from exc
    if not rel.parts or rel.parts[0] != "documents":
        raise DocumentQAError("Document render path must be inside documents storage")
    return rel.as_posix()


def _remote_render(
    docx_path: Path,
    output_dir: Path,
    *,
    worker_url: str,
    worker_token: str,
    data_root: Path,
    timeout_seconds: int,
    max_pages: int,
    raster_dpi: int,
) -> tuple[Path, Path, dict[str, Any]]:
    if not worker_token or worker_token == "change-me-document-worker":
        raise DocumentQAError("Document render worker token is not configured")
    payload = {
        "docx_rel": _data_relative(docx_path, data_root),
        "output_dir_rel": _data_relative(output_dir, data_root),
        "timeout_seconds": int(timeout_seconds),
        "max_pages": int(max_pages),
        "raster_dpi": int(raster_dpi),
    }
    try:
        with httpx.Client(timeout=max(15.0, float(timeout_seconds) + 30.0), trust_env=False) as client:
            response = client.post(worker_url.rstrip("/") + "/render", headers={"X-X1-Document-Token": worker_token, "Accept": "application/json"}, json=payload)
    except httpx.HTTPError as exc:
        raise DocumentQAError("Document render worker is unavailable") from exc
    if response.status_code == 429:
        raise DocumentBusyError("Document renderer is busy; retry shortly")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise DocumentQAError(str(detail or "Document render worker failed")[:1200])
    try:
        result = response.json()
    except ValueError as exc:
        raise DocumentQAError("Document render worker returned invalid JSON") from exc
    pdf = (data_root / str(result.get("pdf_rel") or "")).resolve()
    pages_dir = (data_root / str(result.get("pages_dir_rel") or "")).resolve()
    try:
        pdf.relative_to(output_dir.resolve())
        pages_dir.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise DocumentQAError("Document worker returned path outside revision directory") from exc
    if not pdf.is_file() or not pages_dir.is_dir():
        raise DocumentQAError("Document worker did not produce expected artifacts")
    return pdf, pages_dir, {"renderer": str(result.get("renderer") or "remote"), "page_count": int(result.get("page_count") or 0), "raster_dpi": int(result.get("raster_dpi") or raster_dpi)}


def render_document_artifacts(
    docx_path: Path,
    output_dir: Path,
    *,
    backend: str,
    worker_url: str,
    worker_token: str,
    data_root: Path,
    timeout_seconds: int,
    max_pages: int,
    raster_dpi: int,
) -> tuple[Path, Path, dict[str, Any]]:
    mode = str(backend or "remote").lower()
    if mode == "remote":
        return _remote_render(docx_path, output_dir, worker_url=worker_url, worker_token=worker_token, data_root=data_root, timeout_seconds=timeout_seconds, max_pages=max_pages, raster_dpi=raster_dpi)
    if mode == "local":
        return _local_render(docx_path, output_dir, timeout_seconds=timeout_seconds, raster_dpi=raster_dpi)
    raise DocumentQAError("Unsupported document render backend")


def render_docx_to_pdf(docx_path: Path, output_dir: Path, *, timeout_seconds: int = 60) -> Path:
    """Compatibility wrapper used by development/tests; production uses render_document_artifacts."""
    pdf, _pages, _meta = _local_render(docx_path, output_dir, timeout_seconds=timeout_seconds, raster_dpi=110)
    return pdf


def _text_geometry_qa(pdf_path: Path) -> dict[str, Any]:
    if not shutil.which("pdftotext"):
        return {"status": "not_checked", "issues": [], "word_count": 0}
    try:
        result = subprocess.run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(pdf_path), "-"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "failed", "issues": [{"code": "text_geometry_unavailable"}], "word_count": 0}
    if result.returncode != 0 or not result.stdout.strip():
        return {"status": "failed", "issues": [{"code": "text_geometry_unavailable"}], "word_count": 0}
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return {"status": "failed", "issues": [{"code": "text_geometry_invalid"}], "word_count": 0}
    issues: list[dict[str, Any]] = []
    word_count = 0
    page_no = 0
    for page in [node for node in root.iter() if node.tag.endswith("page")]:
        page_no += 1
        try:
            width = float(page.attrib.get("width", "0")); height = float(page.attrib.get("height", "0"))
        except ValueError:
            width = height = 0.0
        for line in [node for node in page.iter() if node.tag.endswith("line")]:
            words = []
            for word in [node for node in line.iter() if node.tag.endswith("word")]:
                word_count += 1
                try:
                    box = (float(word.attrib["xMin"]), float(word.attrib["yMin"]), float(word.attrib["xMax"]), float(word.attrib["yMax"]))
                except (KeyError, ValueError):
                    continue
                words.append((box, (word.text or "")[:80]))
                x1, y1, x2, y2 = box
                if width > 0 and height > 0 and (x1 < -0.5 or y1 < -0.5 or x2 > width + 0.5 or y2 > height + 0.5):
                    issues.append({"code": "text_outside_page", "page": page_no, "text": (word.text or "")[:80]})
                elif width > 0 and height > 0 and (x1 <= 0.75 or y1 <= 0.75 or x2 >= width - 0.75 or y2 >= height - 0.75):
                    issues.append({"code": "text_clipping_risk", "page": page_no, "text": (word.text or "")[:80]})
            words.sort(key=lambda item: item[0][0])
            for idx in range(1, len(words)):
                prev, current = words[idx - 1], words[idx]
                overlap = prev[0][2] - current[0][0]
                if overlap > 1.5 and prev[1] and current[1]:
                    issues.append({"code": "text_overlap", "page": page_no, "left": prev[1], "right": current[1], "overlap_pt": round(overlap, 2)})
                    if len(issues) >= 40:
                        break
            if len(issues) >= 40:
                break
        if len(issues) >= 40:
            break
    return {"status": "passed" if not issues else "failed", "issues": issues, "word_count": word_count}


def _tile_dark_block(image: Image.Image, *, tile: int = 64) -> tuple[int, int, float] | None:
    gray = image.convert("L")
    for top in range(0, gray.height, tile):
        for left in range(0, gray.width, tile):
            box = (left, top, min(gray.width, left + tile), min(gray.height, top + tile))
            crop = gray.crop(box)
            values = list(crop.getdata())
            if not values:
                continue
            dark = sum(1 for value in values if value < 25) / len(values)
            if dark >= 0.82:
                return left, top, round(dark, 3)
    return None


def render_qa(pdf_path: Path, raster_dir: Path | None = None, *, max_pages: int = 300) -> dict[str, Any]:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        raise DocumentQAError("Rendered PDF cannot be opened") from exc
    page_count = len(reader.pages)
    if page_count < 1:
        raise DocumentQAError("Rendered PDF contains no pages")
    if page_count > max_pages:
        raise DocumentQAError("Rendered PDF exceeds QA page limit")
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        page_info = {"page": index, "text_chars": len(text), "width_pt": round(width, 2), "height_pt": round(height, 2)}
        if width <= 0 or height <= 0:
            issues.append({"code": "invalid_page_geometry", "page": index})
        resources = page.get("/Resources")
        has_xobject = bool(resources and resources.get("/XObject"))
        page_info["has_xobject"] = has_xobject
        if not text and not has_xobject:
            issues.append({"code": "blank_page", "page": index})
        pages.append(page_info)

    raster_status = "not_checked"
    if raster_dir is not None:
        pngs = sorted(raster_dir.glob("page-*.png")) if raster_dir.is_dir() else []
        if len(pngs) != page_count:
            issues.append({"code": "page_rasterization_failed", "expected": page_count, "actual": len(pngs)})
            raster_status = "failed"
        else:
            raster_status = "passed"
            for idx, image_path in enumerate(pngs):
                pages[idx]["raster_sha256"] = sha256_file(image_path)
                pages[idx]["raster_bytes"] = image_path.stat().st_size
                with Image.open(image_path).convert("RGB") as page_image:
                    gray = page_image.convert("L")
                    mask = gray.point(lambda p: 255 if p < 245 else 0)
                    bbox = mask.getbbox()
                    pages[idx]["raster_width_px"] = page_image.width
                    pages[idx]["raster_height_px"] = page_image.height
                    pages[idx]["content_bbox"] = list(bbox) if bbox else None
                    if bbox is None:
                        issues.append({"code": "visually_blank_page", "page": idx + 1})
                    else:
                        left, top, right, bottom = bbox
                        edge_distance = min(left, top, page_image.width - right, page_image.height - bottom)
                        pages[idx]["min_content_edge_distance_px"] = edge_distance
                        if edge_distance <= 2:
                            issues.append({"code": "content_touches_page_edge", "page": idx + 1, "edge_distance_px": edge_distance})
                        elif edge_distance <= 8:
                            warnings.append({"code": "content_near_page_edge", "page": idx + 1, "edge_distance_px": edge_distance})
                    dark = _tile_dark_block(page_image)
                    if dark is not None:
                        issues.append({"code": "suspicious_dark_block", "page": idx + 1, "x": dark[0], "y": dark[1], "dark_ratio": dark[2]})

    geometry = _text_geometry_qa(pdf_path)
    if geometry["status"] == "failed":
        issues.extend(geometry["issues"])
    return {
        "status": "passed" if not issues else "failed",
        "page_count": page_count,
        "issues": issues[:80],
        "warnings": warnings[:80],
        "pages": pages,
        "raster_status": raster_status,
        "text_geometry": geometry,
        "visual_model_status": "deterministic_raster_geometry",
    }


def repair_docx_layout(docx_path: Path, issues: list[dict[str, Any]]) -> dict[str, Any]:
    repairable = {"content_touches_page_edge", "text_clipping_risk", "text_outside_page", "text_overlap"}
    active = {str(item.get("code") or "") for item in issues}
    if not (active & repairable):
        return {"applied": False, "actions": [], "reason": "no_deterministic_repair_for_issue_set"}
    try:
        doc = Document(docx_path)
    except Exception as exc:
        raise DocumentQAError("DOCX cannot be opened for layout repair") from exc
    actions: list[str] = []
    widest = max((len(table.columns) for table in doc.tables), default=0)
    for section in doc.sections:
        section.left_margin = Cm(1.6)
        section.right_margin = Cm(1.6)
        if widest >= 7 and section.orientation != WD_ORIENT.LANDSCAPE:
            old_width, old_height = section.page_width, section.page_height
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = old_height, old_width
            actions.append("landscape_for_wide_tables")
    if doc.tables:
        actions.append("compact_table_layout")
    for table in doc.tables:
        table.autofit = True
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        if run.font.size is None or run.font.size.pt > 8.5:
                            run.font.size = Pt(8.5)
    actions.append("reduced_horizontal_margins")
    doc.save(docx_path)
    return {"applied": True, "actions": list(dict.fromkeys(actions)), "issue_codes": sorted(active & repairable)}
