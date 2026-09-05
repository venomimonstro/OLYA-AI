from __future__ import annotations

import hashlib
import json
import re
import zipfile
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import FileChunk, ProjectFile


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_TOKEN = re.compile(r"[^\W_]{3,}", re.UNICODE)


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


def _extract_docx(content: bytes, max_unpacked_bytes: int = 100 * 1024 * 1024) -> list[ParsedSegment]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        infos = archive.infolist()
        total_unpacked = sum(item.file_size for item in infos)
        if total_unpacked > max_unpacked_bytes:
            raise ValueError("DOCX uncompressed content is too large")
        if "word/document.xml" not in archive.namelist():
            raise ValueError("Invalid DOCX: word/document.xml is missing")
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for node in root.findall(".//w:p", ns):
        parts = [t.text or "" for t in node.findall(".//w:t", ns)]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return [ParsedSegment("\n".join(paragraphs))]


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


def parse_content(filename: str, content: bytes, *, max_pdf_pages: int = 500, max_docx_unpacked_bytes: int = 100 * 1024 * 1024) -> list[ParsedSegment]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".log", ".py", ".js", ".ts", ".php", ".html", ".css", ".yaml", ".yml"}:
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
    if suffix == ".pdf":
        return _extract_pdf(content, max_pages=max_pdf_pages)
    raise ValueError(f"Unsupported file type: {suffix or 'no extension'}")


def chunk_segments(segments: list[ParsedSegment], max_chars: int = 1600, overlap_chars: int = 180) -> list[ParsedSegment]:
    chunks: list[ParsedSegment] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                split = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
                if split > start + max_chars // 2:
                    end = split + 1
            piece = text[start:end].strip()
            if piece:
                chunks.append(ParsedSegment(piece, segment.page_number))
            if end >= len(text):
                break
            start = max(end - overlap_chars, start + 1)
    return chunks


def normalize_terms(text: str) -> set[str]:
    return {item.lower() for item in _TOKEN.findall(text)}


def lexical_score(query: str, text: str) -> float:
    q = normalize_terms(query)
    if not q:
        return 0.0
    terms = normalize_terms(text)
    if not terms:
        return 0.0
    overlap = q & terms
    if not overlap:
        return 0.0
    return (len(overlap) / len(q)) * 10.0 + (len(overlap) / max(len(terms), 1))


def next_file_version(db: Session, project_id: str, logical_name: str) -> int:
    current = db.scalar(select(func.max(ProjectFile.version)).where(ProjectFile.project_id == project_id, ProjectFile.logical_name == logical_name))
    return int(current or 0) + 1


def retrieve_chunks(db: Session, project_id: str, query: str, *, limit: int = 6) -> list[tuple[FileChunk, ProjectFile, float]]:
    terms = list(normalize_terms(query))[:8]
    if not terms:
        return []
    candidate_filter = or_(*(FileChunk.content.ilike(f"%{term}%") for term in terms))
    rows = db.execute(select(FileChunk, ProjectFile).join(ProjectFile, ProjectFile.id == FileChunk.file_id).where(ProjectFile.project_id == project_id, ProjectFile.is_current.is_(True), ProjectFile.status == "ready", candidate_filter).limit(250)).all()
    scored: list[tuple[FileChunk, ProjectFile, float]] = []
    for chunk, file in rows:
        score = lexical_score(query, chunk.content)
        if score > 0:
            scored.append((chunk, file, score))
    scored.sort(key=lambda item: (-item[2], item[0].ordinal))
    return scored[:limit]
