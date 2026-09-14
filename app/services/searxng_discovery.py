from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url


_SITE_FILTER = re.compile(r"(?:^|\s)site:([a-z0-9.-]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+#._-]*")
_STOPWORDS = {
    "кто", "что", "где", "когда", "какой", "какая", "какие", "какое", "сейчас", "сегодня",
    "текущий", "текущая", "текущие", "последний", "последняя", "последние", "найди", "покажи",
    "это", "для", "или", "как", "его", "ее", "её", "при", "про", "из", "по",
    "who", "what", "where", "when", "which", "current", "latest", "today", "now", "the", "of", "for",
    "and", "is", "are", "show", "find", "site", "version",
}


def _site_constraint(query: str) -> str:
    match = _SITE_FILTER.search(str(query or ""))
    return str(match.group(1) if match else "").casefold().strip(".")


def _meaningful_tokens(query: str) -> tuple[str, ...]:
    clean = _SITE_FILTER.sub(" ", str(query or "")).casefold()
    result: list[str] = []
    for token in _TOKEN_RE.findall(clean):
        value = token.strip("._-+")
        if len(value) < 3 or value in _STOPWORDS or value.isdigit() or value in result:
            continue
        result.append(value)
    return tuple(result[:10])


def _relevant(query: str, title: str, url: str, snippet: str) -> bool:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    required = _site_constraint(query)
    if required and not (host == required or host.endswith("." + required)):
        return False
    tokens = _meaningful_tokens(query)
    if not tokens:
        return True
    haystack = " ".join((title, snippet, host, parsed.path)).casefold()
    matches = sum(1 for token in tokens if token in haystack)
    return matches >= (1 if len(tokens) < 3 else 2)


def _payload_marks_engine_unresponsive(payload: dict, engine: str) -> bool:
    target = str(engine or "").casefold()
    for row in payload.get("unresponsive_engines") or []:
        if isinstance(row, str) and target in row.casefold():
            return True
        if isinstance(row, (list, tuple)) and row and target in str(row[0]).casefold():
            return True
        if isinstance(row, dict) and target in str(row.get("engine") or row.get("name") or "").casefold():
            return True
    return False


class SearxngDiscovery:
    """One bounded SearXNG request, TOP-5 only.

    Google/Yandex/DDG/Bing are queried by SearXNG in one request. If that returns
    nothing, a second lightweight DDG/Bing request is allowed. There are no
    engine-by-engine waves, recursive retries or page crawling in discovery.
    """

    name = "searxng"
    primary_engines = ("google", "yandex", "duckduckgo", "bing")
    fallback_engines = ("duckduckgo", "bing")
    general_engines = primary_engines
    max_results = 5

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    async def _request(self, client: httpx.AsyncClient, query: str, engines: tuple[str, ...], language: str | None) -> list[SearchHit]:
        params: dict[str, object] = {
            "q": query,
            "format": "json",
            "safesearch": 1,
            "pageno": 1,
            "categories": "general",
            "engines": ",".join(engines),
        }
        if language:
            params["language"] = language
        response = await client.get(f"{self.base_url}/search", params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        rows: list[SearchHit] = []
        seen: set[str] = set()
        for index, row in enumerate(payload.get("results") or [], start=1):
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            key = canonical_result_url(url)
            if not key or key in seen:
                continue
            title = str(row.get("title") or "")[:320]
            snippet = str(row.get("content") or row.get("snippet") or "")[:700]
            if not _relevant(query, title, url, snippet):
                continue
            seen.add(key)
            source_engines = row.get("engines") or row.get("engine") or []
            if isinstance(source_engines, str):
                provider = source_engines
            elif isinstance(source_engines, list):
                provider = ",".join(str(item) for item in source_engines[:3])
            else:
                provider = ""
            rows.append(SearchHit(
                query=query,
                title=title,
                url=url,
                snippet=snippet,
                rank=len(rows) + 1,
                provider="searxng:" + (provider or "mixed"),
            ))
            if len(rows) >= self.max_results:
                break
        return rows

    async def search(self, query: str, *, count: int = 5, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        if not self.base_url:
            raise DiscoveryError("SearXNG discovery is not configured")
        _ = country
        limit = min(max(int(count), 1), self.max_results)
        timeout = min(self.timeout_seconds, 2.2)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                rows = await self._request(client, query, self.primary_engines, language)
                if not rows:
                    rows = await self._request(client, query, self.fallback_engines, language)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise DiscoveryError("Self-hosted search is temporarily unavailable") from exc
        if not rows:
            raise DiscoveryError("Self-hosted search returned no relevant results")
        return rows[:limit]
