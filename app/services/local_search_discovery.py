from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from app.services.discovery import SearchHit
from app.services.freshness import classify_freshness
from app.services.local_search_store import PageHit, get_local_search_store


_SITE_RE = re.compile(r"(?:^|\s)site:([a-z0-9.-]+)(?=\s|$)", re.I)


def _age_seconds(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _fresh_enough(row: PageHit, *, max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return True
    age = _age_seconds(row.fetched_at)
    return age is not None and age <= max_age_seconds


class LocalSearchDiscovery:
    """Search-provider adapter backed by OLYA's on-disk FTS5 index.

    Stable knowledge can be served from the persistent local copy. Queries that
    explicitly require current data only use local pages while their fetched_at
    timestamp satisfies the freshness contract; otherwise an empty result lets
    ProviderPoolDiscovery continue to SearXNG/Brave. This prevents a stale local
    cache from masking a fresher external result.
    """

    name = "olya_local_index"

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchHit]:
        _ = country, language
        raw = " ".join(str(query or "").split()).strip()
        if not raw:
            return []
        domains = [match.group(1).casefold().removeprefix("www.") for match in _SITE_RE.finditer(raw)]
        clean = _SITE_RE.sub(" ", raw)
        clean = " ".join(clean.split()).strip()
        if len(clean) < 2:
            return []

        verdict = classify_freshness(clean)
        requested = max(1, min(int(count), 20))
        # Fetch a wider local candidate set when freshness filtering may discard
        # old rows; this is still a single cheap SQLite query.
        fetch_limit = min(50, requested * (3 if verdict.required else 1))
        store = get_local_search_store()
        rows = await asyncio.to_thread(
            store.search_pages,
            clean,
            domains=domains,
            limit=fetch_limit,
        )
        if verdict.required:
            rows = [row for row in rows if _fresh_enough(row, max_age_seconds=verdict.max_age_seconds)]
        rows = rows[:requested]

        hits: list[SearchHit] = []
        for index, row in enumerate(rows, 1):
            snippet = row.description or row.content[:1000]
            hits.append(
                SearchHit(
                    query=query,
                    title=row.title or row.domain,
                    url=row.url,
                    snippet=snippet[:2000],
                    rank=index,
                    provider=self.name,
                )
            )
        return hits
