from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.free_serp_discovery import FreeSerpDiscovery


_SITE_FILTER = re.compile(r"(?:^|\s)site:([a-z0-9.-]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+#._-]*")
_STOPWORDS = {
    "кто", "что", "где", "когда", "какой", "какая", "какие", "какое", "сейчас", "сегодня",
    "текущий", "текущая", "текущие", "последний", "последняя", "последние", "найди", "покажи",
    "лучший", "лучшие", "рейтинг", "отзывы", "компания", "компании", "центр", "центры",
    "яндекс", "карты", "2гис", "адрес", "телефон", "сайт",
    "это", "для", "или", "как", "его", "ее", "её", "при", "про", "из", "по",
    "who", "what", "where", "when", "which", "current", "latest", "today", "now", "the", "of", "for",
    "and", "is", "are", "show", "find", "site", "version", "best", "reviews", "rating",
}


def _site_constraint(query: str) -> str:
    match = _SITE_FILTER.search(str(query or ""))
    return str(match.group(1) if match else "").casefold().strip(".")


def _rewrite_map_query(query: str) -> str:
    value = " ".join(str(query or "").split())
    low = value.casefold()
    if "яндекс карт" in low and "site:" not in low:
        value = re.sub(r"яндекс\s+карт\w*", " ", value, flags=re.IGNORECASE)
        return f"site:yandex.ru/maps {value}".strip()
    if "2гис" in low and "site:" not in low:
        value = re.sub(r"2гис", " ", value, flags=re.IGNORECASE)
        return f"site:2gis.ru {value}".strip()
    return value


def _stem(value: str) -> str:
    value = value.casefold().strip("._-+")
    return value[:7] if len(value) >= 7 else value


def _meaningful_tokens(query: str) -> tuple[str, ...]:
    clean = _SITE_FILTER.sub(" ", str(query or "")).casefold()
    result: list[str] = []
    for token in _TOKEN_RE.findall(clean):
        value = token.strip("._-+")
        if len(value) < 3 or value in _STOPWORDS or value.isdigit():
            continue
        stem = _stem(value)
        if stem and stem not in result:
            result.append(stem)
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
    required_matches = 1 if len(tokens) <= 2 else 2
    return matches >= required_matches


class SearxngDiscovery:
    """Self-hosted keyless metasearch with a direct free-SERP safety net."""

    name = "searxng"
    primary_engines = ("google", "yandex", "duckduckgo", "bing")
    fallback_engines = ("duckduckgo", "bing")
    general_engines = primary_engines
    max_results = 12

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.free_fallback = FreeSerpDiscovery(timeout_seconds=min(self.timeout_seconds, 6.0))

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
        relevant_rows: list[SearchHit] = []
        seen: set[str] = set()
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            key = canonical_result_url(url)
            if not key or key in seen:
                continue
            title = str(row.get("title") or "")[:320]
            snippet = str(row.get("content") or row.get("snippet") or "")[:1000]
            if not _relevant(query, title, url, snippet):
                continue
            seen.add(key)
            source_engines = row.get("engines") or row.get("engine") or []
            if isinstance(source_engines, str):
                provider = source_engines
            elif isinstance(source_engines, list):
                provider = ",".join(str(item) for item in source_engines[:4])
            else:
                provider = ""
            relevant_rows.append(SearchHit(
                query=query,
                title=title,
                url=url,
                snippet=snippet,
                rank=len(relevant_rows) + 1,
                provider="searxng:" + (provider or "mixed"),
            ))
            if len(relevant_rows) >= self.max_results:
                break
        return relevant_rows

    async def search(self, query: str, *, count: int = 10, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        effective_query = _rewrite_map_query(query)
        limit = min(max(int(count), 1), self.max_results)
        rows: list[SearchHit] = []

        if self.base_url:
            timeout = min(self.timeout_seconds, 5.5)
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    rows = await self._request(client, effective_query, self.primary_engines, language)
                    if len(rows) < min(3, limit):
                        fallback = await self._request(client, effective_query, self.fallback_engines, language)
                        known = {canonical_result_url(item.url) for item in rows}
                        for item in fallback:
                            key = canonical_result_url(item.url)
                            if key and key not in known:
                                known.add(key)
                                rows.append(item)
                            if len(rows) >= limit:
                                break
            except (httpx.HTTPError, ValueError, TypeError):
                rows = []

        if not rows:
            try:
                rows = await self.free_fallback.search(
                    effective_query,
                    count=limit,
                    country=country,
                    language=language,
                )
            except DiscoveryError as exc:
                raise DiscoveryError("Self-hosted and free SERP search returned no relevant results") from exc

        rows = [item for item in rows if _relevant(effective_query, item.title, item.url, item.snippet)]
        if not rows:
            raise DiscoveryError("Search returned no relevant results")
        return rows[:limit]
