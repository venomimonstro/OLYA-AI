from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import FileChunk, ProjectFile

_TOKEN_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "developer message",
    "reveal your prompt",
    "show hidden prompt",
    "do not follow the user",
    "игнорируй предыдущие инструкции",
    "игнорируй все предыдущие",
    "системный промпт",
    "раскрой системный промпт",
    "покажи скрытые инструкции",
    "не следуй запросу пользователя",
)
_STOP = {
    "and", "the", "for", "with", "that", "this", "from", "как", "что", "для", "это", "или", "при",
    "его", "она", "они", "там", "который", "которая", "которые", "нужно", "надо", "будет", "есть",
}


@dataclass(frozen=True)
class RagHit:
    chunk: FileChunk
    file: ProjectFile
    score: float
    anchor: bool = True


def normalize_text(text: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def terms(text: str) -> list[str]:
    values = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(normalize_text(text)):
        if token in _STOP or token in seen:
            continue
        seen.add(token)
        values.append(token)
    return values


def suspicious_instructions(text: str) -> list[str]:
    normalized = normalize_text(text)
    return [marker for marker in _INJECTION_MARKERS if marker in normalized]


def _query_phrases(query: str) -> list[str]:
    normalized = normalize_text(query)
    quoted = [normalize_text(item) for item in re.findall(r'["«](.*?)["»]', query) if item.strip()]
    words = terms(query)
    if 2 <= len(words) <= 8:
        quoted.append(" ".join(words))
    if normalized and len(normalized) <= 180:
        quoted.append(normalized)
    return list(dict.fromkeys(item for item in quoted if len(item) >= 4))[:5]


def hybrid_score(query: str, text: str, *, logical_name: str = "") -> float:
    q_terms = terms(query)
    if not q_terms:
        return 0.0
    normalized = normalize_text(text)
    doc_terms = terms(text)
    if not doc_terms:
        return 0.0
    counts: dict[str, int] = {}
    for token in doc_terms:
        counts[token] = counts.get(token, 0) + 1
    matched = [token for token in q_terms if token in counts]
    if not matched:
        return 0.0

    # A compact BM25-like lexical score that needs no resident vector model.
    length_norm = 0.55 + 0.45 * (len(doc_terms) / 180.0)
    tf_score = sum((counts[token] * 2.2) / (counts[token] + 1.2 * length_norm) for token in matched)
    coverage = len(matched) / max(1, len(q_terms))
    density = len(matched) / max(12, len(set(doc_terms)))
    phrase_bonus = sum(1.8 for phrase in _query_phrases(query) if phrase in normalized)
    name_terms = set(terms(logical_name))
    name_bonus = min(2.0, 0.65 * len(name_terms & set(q_terms)))
    start_bonus = 0.4 if any(normalized.startswith(token) for token in matched) else 0.0
    return round(tf_score + coverage * 8.0 + density * 4.0 + phrase_bonus + name_bonus + start_bonus, 5)


def _fingerprint(text: str) -> set[str]:
    words = terms(text)
    if len(words) < 5:
        return set(words)
    return {" ".join(words[index:index + 4]) for index in range(len(words) - 3)}


def near_duplicate(left: str, right: str, *, threshold: float = 0.82) -> bool:
    a = _fingerprint(left)
    b = _fingerprint(right)
    if not a or not b:
        return normalize_text(left) == normalize_text(right)
    return len(a & b) / max(1, len(a | b)) >= threshold


def _diversify(scored: list[RagHit], *, limit: int, per_file: int = 3) -> list[RagHit]:
    selected: list[RagHit] = []
    file_counts: dict[str, int] = {}
    for item in scored:
        if file_counts.get(item.file.id, 0) >= per_file:
            continue
        if any(near_duplicate(item.chunk.content, old.chunk.content) for old in selected):
            continue
        selected.append(item)
        file_counts[item.file.id] = file_counts.get(item.file.id, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def retrieve(
    db: Session,
    project_id: str,
    query: str,
    *,
    limit: int = 6,
    candidate_limit: int = 360,
    neighbor_window: int = 1,
) -> list[RagHit]:
    q_terms = terms(query)[:12]
    if not q_terms:
        return []
    candidate_filter = or_(*(FileChunk.content.ilike(f"%{token}%") for token in q_terms))
    rows = db.execute(
        select(FileChunk, ProjectFile)
        .join(ProjectFile, ProjectFile.id == FileChunk.file_id)
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.is_current.is_(True),
            ProjectFile.status == "ready",
            candidate_filter,
        )
        .limit(max(limit * 10, candidate_limit))
    ).all()

    scored: list[RagHit] = []
    file_map: dict[str, ProjectFile] = {}
    for chunk, file in rows:
        file_map[file.id] = file
        score = hybrid_score(query, chunk.content, logical_name=file.logical_name)
        if score > 0:
            scored.append(RagHit(chunk=chunk, file=file, score=score, anchor=True))
    scored.sort(key=lambda item: (-item.score, item.file.logical_name.casefold(), item.chunk.ordinal))
    anchors = _diversify(scored, limit=max(limit, min(limit * 2, 12)))
    if not anchors:
        return []

    # Pull one neighbor around strong anchors so the model sees local context, not
    # an isolated sentence. Neighbor hits never outrank anchors and are still
    # subject to deduplication and the final bounded result count.
    if neighbor_window > 0:
        wanted: dict[str, set[int]] = {}
        for hit in anchors[:limit]:
            values = wanted.setdefault(hit.file.id, set())
            for offset in range(-neighbor_window, neighbor_window + 1):
                if offset:
                    values.add(max(0, hit.chunk.ordinal + offset))
        for file_id, ordinals in wanted.items():
            if not ordinals:
                continue
            file = file_map.get(file_id)
            if file is None:
                continue
            neighbors = db.scalars(
                select(FileChunk).where(FileChunk.file_id == file_id, FileChunk.ordinal.in_(ordinals))
            ).all()
            for chunk in neighbors:
                if any(existing.chunk.id == chunk.id for existing in anchors):
                    continue
                nearest = max((hit.score for hit in anchors if hit.file.id == file_id and abs(hit.chunk.ordinal - chunk.ordinal) <= neighbor_window), default=0.0)
                anchors.append(RagHit(chunk=chunk, file=file, score=round(nearest * 0.58, 5), anchor=False))

    anchors.sort(key=lambda item: (-item.score, not item.anchor, item.file.logical_name.casefold(), item.chunk.ordinal))
    return _diversify(anchors, limit=limit, per_file=4)


def outline_for_hits(hits: Iterable[RagHit], *, max_lines_per_file: int = 5) -> dict[str, list[str]]:
    outlines: dict[str, list[str]] = {}
    for hit in hits:
        bucket = outlines.setdefault(hit.file.logical_name, [])
        if len(bucket) >= max_lines_per_file:
            continue
        for raw in hit.chunk.content.splitlines():
            line = raw.strip()
            if not line:
                continue
            looks_like_heading = (
                line.startswith("#")
                or line.lower().startswith(("sheet:", "section:", "chapter:", "раздел:", "глава:"))
                or (len(line) <= 100 and line.endswith(":"))
            )
            if looks_like_heading and line not in bucket:
                bucket.append(line[:160])
                break
    return {name: lines for name, lines in outlines.items() if lines}
