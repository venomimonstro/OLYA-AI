from __future__ import annotations

import asyncio
import re

from app.services.discovery import SearchHit
from app.services.local_search_store import get_local_search_store


_SITE_RE = re.compile(r"(?:^|\s)site:([a-z0-9.-]+)(?=\s|$)", re.I)


class LocalSearchDiscovery:
    """Search-provider adapter backed by OLYA's on-disk FTS5 index.

    It intentionally returns an empty successful result when the local index has
    no match. ProviderPoolDiscovery will then continue to SearXNG/Brave. When a
    local match exists, no network search is needed for the ordinary fast path.
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
        store = get_local_search_store()
        rows = await asyncio.to_thread(
            store.search_pages,
            clean,
            domains=domains,
            limit=max(1, min(int(count), 20)),
        )
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
