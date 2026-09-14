from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url


_SITE_FILTER = re.compile(r"(?:^|\s)site:([a-z0-9.-]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+#._-]*")
_STOPWORDS = {
    "кто", "что", "где", "когда", "какой", "какая", "какие", "какое", "сейчас", "сегодня",
    "текущий", "текущая", "текущие", "последний", "последняя", "последние", "найди", "покажи",
    "написал", "автор", "это", "для", "или", "как", "его", "ее", "её", "при", "про", "из", "по",
    "who", "what", "where", "when", "which", "current", "latest", "today", "now", "the", "of", "for",
    "and", "is", "are", "was", "were", "show", "find", "site", "version",
}


def _site_constraint(query: str) -> str:
    match = _SITE_FILTER.search(str(query or ""))
    return str(match.group(1) if match else "").casefold().strip(".")


def _meaningful_tokens(query: str) -> tuple[str, ...]:
    clean = _SITE_FILTER.sub(" ", str(query or "")).casefold()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(clean):
        value = token.strip("._-+")
        if len(value) < 3 or value in _STOPWORDS or value.isdigit():
            continue
        if value not in tokens:
            tokens.append(value)
    return tuple(tokens[:10])


def _host_matches(host: str, expected: str) -> bool:
    return bool(expected and (host == expected or host.endswith("." + expected)))


def _relevance_score(query: str, *, title: str, url: str, snippet: str) -> float:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    required_host = _site_constraint(query)
    if required_host and not _host_matches(host, required_host):
        return 0.0

    tokens = _meaningful_tokens(query)
    base = 0.72 if required_host else 0.0
    if not tokens:
        return max(base, 0.55 if required_host else 0.25)

    haystack = " ".join((title, snippet, host, parsed.path)).casefold()
    matches = sum(1 for token in tokens if token in haystack)
    ratio = matches / max(len(tokens), 1)

    if not required_host:
        if matches == 0:
            return 0.0
        # Entity-rich queries should not accept a generic homepage that happens
        # to contain one broad word.
        if len(tokens) >= 3 and matches == 1:
            return 0.0
        base = 0.48

    title_cf = str(title or "").casefold()
    title_matches = sum(1 for token in tokens if token in title_cf)
    return min(1.0, base + ratio * 0.42 + min(title_matches, 3) * 0.06)


class SearxngDiscovery:
    """Bounded, relevance-filtered metasearch for interactive chat.

    Only engines that have remained usable from the production server are in the
    interactive pool. A blocked engine must not create a latency storm or consume
    the whole answer budget. SearXNG itself remains replaceable behind this class.
    """

    name = "searxng"
    general_engines = ("bing", "startpage")

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self._failures: dict[str, int] = {engine: 0 for engine in self.general_engines}
        self._suspended_until: dict[str, float] = {engine: 0.0 for engine in self.general_engines}

    @staticmethod
    def _merge_hits(per_engine: dict[str, list[SearchHit]], *, limit: int) -> list[SearchHit]:
        by_url: dict[str, SearchHit] = {}
        providers: dict[str, list[str]] = {}
        order: list[str] = []
        max_len = max((len(rows) for rows in per_engine.values()), default=0)
        engines = [name for name in SearxngDiscovery.general_engines if name in per_engine]
        for index in range(max_len):
            for engine in engines:
                rows = per_engine.get(engine) or []
                if index >= len(rows):
                    continue
                hit = rows[index]
                key = canonical_result_url(hit.url)
                if not key:
                    continue
                if key not in by_url:
                    by_url[key] = hit
                    providers[key] = []
                    order.append(key)
                if engine not in providers[key]:
                    providers[key].append(engine)
        result: list[SearchHit] = []
        for key in order:
            base = by_url[key]
            source_engines = providers.get(key) or []
            result.append(SearchHit(
                query=base.query,
                title=base.title,
                url=base.url,
                snippet=base.snippet,
                rank=len(result) + 1,
                provider="searxng:" + ",".join(source_engines[:2]) if source_engines else "searxng",
            ))
            if len(result) >= limit:
                break
        return result

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchHit]:
        if not self.base_url:
            raise DiscoveryError("SearXNG discovery is not configured")
        _ = country
        count = min(max(int(count), 1), 12)
        loop = asyncio.get_running_loop()
        now = loop.time()
        engines = [engine for engine in self.general_engines if self._suspended_until.get(engine, 0.0) <= now]
        if not engines:
            # Do not remain permanently dark after a transient outage.
            engines = [min(self.general_engines, key=lambda e: self._suspended_until.get(e, 0.0))]

        global_budget = min(self.timeout_seconds, 2.6)
        per_engine_timeout = min(self.timeout_seconds, 2.2)
        deadline = loop.time() + global_budget
        results: dict[str, list[SearchHit]] = {}

        async with httpx.AsyncClient(timeout=per_engine_timeout, trust_env=False) as client:
            async def one(engine: str) -> tuple[str, list[SearchHit], bool]:
                params: dict[str, object] = {
                    "q": query,
                    "format": "json",
                    "safesearch": 1,
                    "pageno": 1,
                    "categories": "general",
                    "engines": engine,
                }
                if language:
                    params["language"] = language
                try:
                    response = await client.get(
                        f"{self.base_url}/search",
                        params=params,
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    return engine, [], False

                hits: list[SearchHit] = []
                for index, row in enumerate(payload.get("results") or [], start=1):
                    if not isinstance(row, dict):
                        continue
                    url = str(row.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        continue
                    title = str(row.get("title") or "")[:500]
                    snippet = str(row.get("content") or row.get("snippet") or "")[:1200]
                    if _relevance_score(query, title=title, url=url, snippet=snippet) <= 0:
                        continue
                    hits.append(SearchHit(
                        query=query,
                        title=title,
                        url=url,
                        snippet=snippet,
                        rank=index,
                        provider=f"searxng:{engine}",
                    ))
                    if len(hits) >= count:
                        break
                return engine, hits, True

            pending = {asyncio.create_task(one(engine)) for engine in engines}
            try:
                while pending:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending = await asyncio.wait(
                        pending,
                        timeout=min(0.25, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        try:
                            engine, hits, transport_ok = task.result()
                        except Exception:
                            continue
                        if transport_ok:
                            self._failures[engine] = 0
                        else:
                            failures = self._failures.get(engine, 0) + 1
                            self._failures[engine] = failures
                            if failures >= 2:
                                self._suspended_until[engine] = loop.time() + 120.0
                        if hits:
                            results[engine] = hits
                    merged = self._merge_hits(results, limit=count)
                    if len(results) >= 2 and len(merged) >= min(count, 4):
                        break
                    # A primary-site query needs one correct official hit, not a
                    # second search engine merely for decoration.
                    if _site_constraint(query) and merged:
                        break
            finally:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        merged = self._merge_hits(results, limit=count)
        if not merged:
            raise DiscoveryError("Self-hosted search providers returned no relevant results")
        return merged
