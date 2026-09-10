from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FileChunk, ProjectFile
from app.services.rag_v2 import hybrid_score, retrieve


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_TOKEN = re.compile(r"[^\W_]{3,}", re.UNICODE)
_CODE_BOUNDARY = re.compile(
    r"^(?:async\s+def|def|class|function|export\s+(?:async\s+)?function|interface|type|struct|func|fn|public\s+class|private\s+class)\b",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^(?:#{1,6}\s+|(?:глава|раздел|chapter|section)\s+\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedSegment:
    text: str
    page_number: int | None = None


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_filename(filename: str) -> str:
    name = unicodedata.normalize("NFKC", filename).replace("\\", "/")
    name = name.rsplit("/", 1)[-1].strip() or "file"
    name = _CONTROL.sub("_", name).replace("..", ".")
    return name[:240]


def storage_path(root: Path, project_id: str, file_id: str, version: int, filename: str) -> Path:
    return root / project_id / file_id / f"v{version}" / safe_filename(filename)


def _zip_guard(archive: zipfile.ZipFile, max_unpacked_bytes: int) -> None:
    infos = archive.infolist()
    total = 0
    for item in infos:
        if item.is_dir():
            continue
        name = item.filename.replace("\\", "/")
        if name.startswith("/") or "../" in f"/{name}" or name.startswith("../"):
            raise ValueError("Unsafe archive path")
        total += max(0, int(item.file_size))
        if total > max_unpacked_bytes:
            raise ValueError("Office document uncompressed content is too large")
        if item.compress_size > 0 and item.file_size > 10 * 1024 * 1024:
            ratio = item.file_size / max(1, item.compress_size)
            if ratio > 250:
                raise ValueError("Office document compression ratio is unsafe")


def _extract_docx(content: bytes, max_unpacked_bytes: int = 100 * 1024 * 1024) -> list[ParsedSegment]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        _zip_guard(archive, max_unpacked_bytes)
        if "word/document.xml" not in archive.namelist():
            raise ValueError("Invalid DOCX: word/document.xml is missing")
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for node in root.findall(".//w:p", ns):
        parts = [t.text or "" for t in node.findall(".//w:t", ns)]
        text = "".join(parts).strip()
        if not text:
            continue
        style = node.find("./w:pPr/w:pStyle", ns)
        style_name = style.attrib.get(f"{{{ns['w']}}}val", "") if style is not None else ""
        if style_name.casefold().startswith(("heading", "заголовок")):
            digits = re.findall(r"\d+", style_name)
            level = min(6, max(1, int(digits[0]) if digits else 2))
            text = "#" * level + " " + text
        paragraphs.append(text)
    return [ParsedSegment("\n\n".join(paragraphs))] if paragraphs else []


def _extract_pdf(content: bytes, max_pages: int = 500) -> list[ParsedSegment]:
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF has too many pages: {len(reader.pages)} > {max_pages}")
    result: list[ParsedSegment] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            result.append(ParsedSegment(text=text, page_number=index))
    return result


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read(name))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result: list[str] = []
    for node in root.findall("x:si", ns):
        result.append("".join((item.text or "") for item in node.findall(".//x:t", ns)))
    return result


def _xlsx_sheet_names(archive: zipfile.ZipFile) -> dict[str, str]:
    result: dict[str, str] = {}
    if "xl/workbook.xml" not in archive.namelist() or "xl/_rels/workbook.xml.rels" not in archive.namelist():
        return result
    wb = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    main_ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    pkg_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel_map = {
        node.attrib.get("Id", ""): node.attrib.get("Target", "")
        for node in rels.findall(f"{{{pkg_rel}}}Relationship")
    }
    rid_attr = f"{{{rel_ns['r']}}}id"
    for sheet in wb.findall(".//x:sheet", main_ns):
        rid = sheet.attrib.get(rid_attr, "")
        target = rel_map.get(rid, "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = "xl/" + target.lstrip("/")
        path = str(Path(path)).replace("\\", "/")
        result[path] = sheet.attrib.get("name", Path(path).stem)
    return result


def _extract_xlsx(content: bytes, max_unpacked_bytes: int = 100 * 1024 * 1024) -> list[ParsedSegment]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        _zip_guard(archive, max_unpacked_bytes)
        shared = _xlsx_shared_strings(archive)
        sheet_names = _xlsx_sheet_names(archive)
        worksheet_paths = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        )
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        segments: list[ParsedSegment] = []
        for index, path in enumerate(worksheet_paths, start=1):
            root = ElementTree.fromstring(archive.read(path))
            lines = [f"Sheet: {sheet_names.get(path, f'Sheet {index}')}"]
            for row in root.findall(".//x:sheetData/x:row", ns):
                values: list[str] = []
                for cell in row.findall("x:c", ns):
                    kind = cell.attrib.get("t", "")
                    value_node = cell.find("x:v", ns)
                    inline = cell.find("x:is", ns)
                    value = ""
                    if kind == "inlineStr" and inline is not None:
                        value = "".join((item.text or "") for item in inline.findall(".//x:t", ns))
                    elif value_node is not None:
                        raw = value_node.text or ""
                        if kind == "s":
                            try:
                                value = shared[int(raw)]
                            except (ValueError, IndexError):
                                value = raw
                        else:
                            value = raw
                    values.append(value.replace("\t", " ").replace("\n", " ").strip())
                if any(values):
                    lines.append("\t".join(values).rstrip())
            if len(lines) > 1:
                segments.append(ParsedSegment("\n".join(lines)))
        return segments


def parse_content(
    filename: str,
    content: bytes,
    *,
    max_pdf_pages: int = 500,
    max_docx_unpacked_bytes: int = 100 * 1024 * 1024,
) -> list[ParsedSegment]:
    suffix = Path(filename).suffix.lower()
    text_types = {
        ".txt", ".md", ".csv", ".tsv", ".log", ".py", ".js", ".jsx", ".ts", ".tsx", ".php",
        ".html", ".css", ".scss", ".yaml", ".yml", ".sql", ".sh", ".bash", ".go", ".rs", ".java",
        ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".toml", ".ini", ".conf", ".xml",
    }
    if suffix in text_types:
        return [ParsedSegment(content.decode("utf-8", errors="replace"))]
    if suffix == ".json":
        decoded = content.decode("utf-8", errors="replace")
        try:
            value = json.loads(decoded)
            return [ParsedSegment(json.dumps(value, ensure_ascii=False, indent=2))]
        except json.JSONDecodeError:
            return [ParsedSegment(decoded)]
    if suffix == ".docx":
        return _extract_docx(content, max_unpacked_bytes=max_docx_unpacked_bytes)
    if suffix == ".xlsx":
        return _extract_xlsx(content, max_unpacked_bytes=max_docx_unpacked_bytes)
    if suffix == ".pdf":
        return _extract_pdf(content, max_pages=max_pdf_pages)
    raise ValueError(f"Unsupported file type: {suffix or 'no extension'}")


def _units(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    units: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            value = "\n".join(current).strip()
            if value:
                units.append(value)
            current.clear()

    for line in lines:
        stripped = line.strip()
        boundary = bool(_HEADING.match(stripped) or _CODE_BOUNDARY.match(stripped) or stripped.lower().startswith("sheet:"))
        if boundary:
            flush()
            current.append(line)
            continue
        if not stripped:
            flush()
            continue
        current.append(line)
    flush()
    return units


def _split_long_unit(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split = max(text.rfind("\n", start, end), text.rfind(". ", start, end), text.rfind(", ", start, end))
            if split > start + max_chars // 2:
                end = split + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = end
    return pieces


def chunk_segments(segments: list[ParsedSegment], max_chars: int = 1600, overlap_chars: int = 180) -> list[ParsedSegment]:
    """Structure-aware bounded chunking for prose, tables and source code."""
    max_chars = max(400, int(max_chars))
    overlap_chars = max(0, min(int(overlap_chars), max_chars // 4))
    chunks: list[ParsedSegment] = []
    for segment in segments:
        source_units: list[str] = []
        for unit in _units(segment.text.strip()):
            source_units.extend(_split_long_unit(unit, max_chars))
        buffer = ""
        for unit in source_units:
            candidate = unit if not buffer else buffer + "\n\n" + unit
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                chunks.append(ParsedSegment(buffer.strip(), segment.page_number))
                overlap = buffer[-overlap_chars:].strip() if overlap_chars else ""
                buffer = (overlap + "\n" + unit).strip() if overlap else unit
                if len(buffer) > max_chars:
                    for piece in _split_long_unit(buffer, max_chars):
                        chunks.append(ParsedSegment(piece, segment.page_number))
                    buffer = ""
            else:
                chunks.append(ParsedSegment(unit[:max_chars].strip(), segment.page_number))
        if buffer.strip():
            chunks.append(ParsedSegment(buffer.strip(), segment.page_number))
    return chunks


def normalize_terms(text: str) -> set[str]:
    return {item.lower() for item in _TOKEN.findall(text)}


def lexical_score(query: str, text: str) -> float:
    # Backward-compatible public helper now delegates to Sprint 46 hybrid scoring.
    return hybrid_score(query, text)


def next_file_version(db: Session, project_id: str, logical_name: str) -> int:
    current = db.scalar(
        select(func.max(ProjectFile.version)).where(
            ProjectFile.project_id == project_id,
            ProjectFile.logical_name == logical_name,
        )
    )
    return int(current or 0) + 1


def retrieve_chunks(
    db: Session,
    project_id: str,
    query: str,
    *,
    limit: int = 6,
) -> list[tuple[FileChunk, ProjectFile, float]]:
    return [(hit.chunk, hit.file, hit.score) for hit in retrieve(db, project_id, query, limit=limit)]


def recover_stale_processing(
    db: Session,
    *,
    project_id: str | None = None,
    timeout_seconds: int = 900,
    now: datetime | None = None,
) -> int:
    """Make interrupted parser jobs visible and safely retryable."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(seconds=max(60, int(timeout_seconds)))
    stmt = select(ProjectFile).where(
        ProjectFile.status == "processing",
        ProjectFile.created_at < cutoff,
    )
    if project_id is not None:
        stmt = stmt.where(ProjectFile.project_id == project_id)
    rows = list(db.scalars(stmt.order_by(ProjectFile.created_at).limit(200)).all())
    for row in rows:
        row.status = "error"
        row.error_message = "File processing was interrupted or timed out; use Retry to process the stored file again"
        row.is_current = False
    return len(rows)
