from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.services.discovery import canonical_result_url

_GENERIC = {
    "официальный", "сайт", "отзывы", "отзыв", "пациентов", "пациента", "пациент",
    "цены", "цена", "рейтинг", "стоматология", "клиника", "центр", "самара",
}
_RATING_RE = re.compile(r"(?<!\d)([1-4][\.,]\d|5(?:[\.,]0)?)(?:\s*(?:из|/|из\s+)?\s*5)?(?!\d)", re.I)
_REVIEW_RE = re.compile(r"(?<!\d)(\d{1,7})\s+(?:отзыв(?:а|ов)?|оцен(?:ка|ки|ок)|reviews?)\b", re.I)


def normalize_business_name(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = _REVIEW_RE.sub(" ", text)
    text = _RATING_RE.sub(" ", text)
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", " ", text).casefold()
    words = [w for w in text.split() if w not in _GENERIC and len(w) > 1]
    return " ".join(words[:8]).strip()


def root_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def candidate_key(row: dict) -> str:
    kind = row.get("source_kind", "web")
    host = root_host(str(row.get("url") or ""))
    name = normalize_business_name(str(row.get("title") or ""))
    if kind == "official_candidate" and host:
        return f"host:{host}"
    if name:
        return f"name:{name}"
    return f"url:{canonical_result_url(str(row.get('url') or ''))}"


def extract_public_rating(row: dict) -> tuple[float | None, int | None]:
    if row.get("source_kind") not in {"reviews", "maps_catalog"}:
        return None, None
    text = f"{row.get('title', '')} {row.get('snippet', '')}"
    rating_match = _RATING_RE.search(text)
    if not rating_match:
        return None, None
    rating = float(rating_match.group(1).replace(",", "."))
    if not 1.0 <= rating <= 5.0:
        return None, None
    review_match = _REVIEW_RE.search(text)
    reviews = int(review_match.group(1)) if review_match else None
    return rating, reviews


def _reputation_signal(items: list[dict]) -> tuple[float | None, int, int]:
    observations: list[tuple[float, int]] = []
    for item in items:
        rating, reviews = extract_public_rating(item)
        if rating is not None:
            observations.append((rating, reviews or 1))
    if not observations:
        return None, 0, 0
    weights = [1.0 + math.log10(max(count, 1)) for _, count in observations]
    weighted = sum(r * w for (r, _), w in zip(observations, weights)) / sum(weights)
    total_reviews = sum(count for _, count in observations)
    return round(weighted, 3), len(observations), total_reviews


@dataclass(frozen=True)
class BusinessCandidate:
    key: str
    display_name: str
    official_url: str | None
    source_urls: list[str]
    source_kinds: list[str]
    provider_count: int
    independent_source_count: int
    conflict_flags: list[str]
    evidence_score: float
    recommendation_state: str
    public_rating: float | None
    rating_source_count: int
    review_count_total: int
    comparison_score: float | None
    comparison_state: str


def build_business_candidates(rows: list[dict]) -> list[BusinessCandidate]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("url"):
            groups[candidate_key(row)].append(row)

    result: list[BusinessCandidate] = []
    for key, items in groups.items():
        urls = list(dict.fromkeys(canonical_result_url(str(i["url"])) for i in items))
        kinds = sorted(set(str(i.get("source_kind") or "web") for i in items))
        providers = set(str(i.get("provider") or "unknown") for i in items)
        official = next((canonical_result_url(str(i["url"])) for i in items if i.get("source_kind") == "official_candidate"), None)
        names = [str(i.get("title") or "").strip() for i in items if str(i.get("title") or "").strip()]
        display = min(names, key=len) if names else root_host(urls[0])
        independent = sum(1 for k in kinds if k in {"reviews", "maps_catalog", "web"})
        conflicts: list[str] = []
        normalized_names = {normalize_business_name(n) for n in names if normalize_business_name(n)}
        if len(normalized_names) > 2:
            conflicts.append("identity_name_conflict")
        if len({root_host(u) for u in urls if root_host(u)}) > 3 and official is None:
            conflicts.append("identity_domain_ambiguous")

        evidence = 0.18 + (0.25 if official else 0) + min(len(kinds), 4) * 0.10 + min(len(providers), 3) * 0.05
        if independent >= 2:
            evidence += 0.12
        evidence -= len(conflicts) * 0.18
        evidence = round(max(0.0, min(evidence, 0.95)), 3)
        rec_state = "well_supported" if evidence >= 0.72 and not conflicts else ("supported" if evidence >= 0.48 else "insufficient_evidence")

        public_rating, rating_sources, total_reviews = _reputation_signal(items)
        if public_rating is None:
            comparison_score = None
            comparison_state = "insufficient_data"
        else:
            # 0..100 quality signal. Rating contributes 70%, evidence confidence 30%.
            comparison_score = round(((public_rating - 1.0) / 4.0) * 70.0 + evidence * 30.0, 2)
            comparison_state = "comparable" if rating_sources >= 2 and evidence >= 0.60 else "preliminary"

        result.append(BusinessCandidate(
            key=key, display_name=display[:300], official_url=official, source_urls=urls, source_kinds=kinds,
            provider_count=len(providers), independent_source_count=independent, conflict_flags=conflicts,
            evidence_score=evidence, recommendation_state=rec_state, public_rating=public_rating,
            rating_source_count=rating_sources, review_count_total=total_reviews,
            comparison_score=comparison_score, comparison_state=comparison_state,
        ))
    result.sort(key=lambda x: ((x.comparison_score if x.comparison_score is not None else -1), x.evidence_score), reverse=True)
    return result


def recommendation_summary(candidates: list[BusinessCandidate], *, limit: int = 5) -> dict:
    comparable = [c for c in candidates if c.comparison_state == "comparable" and not c.conflict_flags]
    top = comparable[:limit]
    decisive = False
    if len(top) >= 2:
        decisive = bool(top[0].comparison_score is not None and top[1].comparison_score is not None and top[0].evidence_score >= 0.72 and (top[0].comparison_score - top[1].comparison_score) >= 8.0)
    elif len(top) == 1:
        decisive = top[0].evidence_score >= 0.82 and (top[0].comparison_score or 0) >= 82
    return {
        "decisive_winner": decisive,
        "message": "Есть достаточно независимых данных для уверенного лидера." if decisive else "Недостаточно оснований объявлять одну компанию однозначно лучшей; показываем наиболее подтвержденные варианты и качество данных.",
        "candidate_keys": [c.key for c in (top or candidates[:limit])],
    }
