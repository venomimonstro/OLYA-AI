from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.services.local_search_store import LocalSearchStore, get_local_search_store


@dataclass(frozen=True)
class BusinessQuality:
    rating: float | None
    reviews: int | None
    source_updated_at: str


def _store(value: LocalSearchStore | None = None) -> LocalSearchStore:
    store = value or get_local_search_store()
    store.ensure_schema()
    return store


def ensure_business_quality_schema(store: LocalSearchStore | None = None) -> None:
    """Add ranking metadata to existing owned-search databases in place.

    OLYA already has persistent installations with populated `businesses` rows,
    so this is intentionally an additive SQLite migration rather than a rebuild.
    """
    target = _store(store)
    connection = sqlite3.connect(str(target.path), timeout=15.0)
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(businesses)")}
        if "rating" not in columns:
            connection.execute("ALTER TABLE businesses ADD COLUMN rating REAL")
        if "review_count" not in columns:
            connection.execute("ALTER TABLE businesses ADD COLUMN review_count INTEGER")
        if "source_updated_at" not in columns:
            connection.execute("ALTER TABLE businesses ADD COLUMN source_updated_at TEXT NOT NULL DEFAULT ''")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_rating ON businesses(city, category, rating DESC, review_count DESC)"
        )
        connection.commit()
    finally:
        connection.close()


def _rating(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0.0 <= result <= 5.0 else None


def _reviews(value: object) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, result)


def save_business_quality(
    business_id: int,
    *,
    rating: object = None,
    reviews: object = None,
    source_updated_at: str = "",
    store: LocalSearchStore | None = None,
) -> None:
    target = _store(store)
    ensure_business_quality_schema(target)
    normalized_rating = _rating(rating)
    normalized_reviews = _reviews(reviews)
    connection = sqlite3.connect(str(target.path), timeout=15.0)
    try:
        connection.execute(
            """
            UPDATE businesses
            SET rating=COALESCE(?, rating),
                review_count=COALESCE(?, review_count),
                source_updated_at=CASE WHEN ?!='' THEN ? ELSE source_updated_at END
            WHERE id=?
            """,
            (
                normalized_rating,
                normalized_reviews,
                str(source_updated_at or "")[:80],
                str(source_updated_at or "")[:80],
                int(business_id),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def load_business_quality(
    business_ids: Iterable[int], *, store: LocalSearchStore | None = None
) -> dict[int, BusinessQuality]:
    ids = sorted({int(value) for value in business_ids if int(value) > 0})
    if not ids:
        return {}
    target = _store(store)
    ensure_business_quality_schema(target)
    placeholders = ",".join("?" for _ in ids)
    connection = sqlite3.connect(str(target.path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"SELECT id,rating,review_count,source_updated_at FROM businesses WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        connection.close()
    return {
        int(row["id"]): BusinessQuality(
            rating=float(row["rating"]) if row["rating"] is not None else None,
            reviews=int(row["review_count"]) if row["review_count"] is not None else None,
            source_updated_at=str(row["source_updated_at"] or ""),
        )
        for row in rows
    }


def quality_score(rating: float | None, reviews: int | None) -> float:
    """Conservative Bayesian score for 'best/top' queries.

    A 5.0 based on one review must not outrank a 4.8 based on hundreds of
    reviews. The prior is deliberately strong enough to suppress tiny samples,
    while a small logarithmic volume bonus separates mature businesses with
    similar adjusted ratings.
    """
    if rating is None:
        return -1.0
    count = max(0, int(reviews or 0))
    effective_count = max(1, count)
    prior_rating = 4.2
    prior_weight = 12.0
    bayesian = (float(rating) * effective_count + prior_rating * prior_weight) / (
        effective_count + prior_weight
    )
    volume_bonus = min(math.log10(count + 1) * 0.025, 0.09)
    return bayesian + volume_bonus


def ranked_business_ids(
    *,
    city: str,
    category: str,
    limit: int = 500,
    store: LocalSearchStore | None = None,
) -> list[int]:
    target = _store(store)
    ensure_business_quality_schema(target)
    connection = sqlite3.connect(str(target.path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id,rating,review_count,confidence
            FROM businesses
            WHERE city=? AND category=?
            ORDER BY
                CASE WHEN rating IS NULL THEN 1 ELSE 0 END ASC,
                rating DESC,
                COALESCE(review_count,0) DESC,
                confidence DESC
            LIMIT ?
            """,
            (str(city), str(category), max(1, min(int(limit), 1000))),
        ).fetchall()
    finally:
        connection.close()
    ranked = sorted(
        rows,
        key=lambda row: (
            quality_score(
                float(row["rating"]) if row["rating"] is not None else None,
                int(row["review_count"]) if row["review_count"] is not None else None,
            ),
            int(row["review_count"] or 0),
            float(row["confidence"] or 0.0),
        ),
        reverse=True,
    )
    return [int(row["id"]) for row in ranked]
