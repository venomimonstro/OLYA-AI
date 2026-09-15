from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url, dedupe_hits


_BLOCKED_MARKERS = (
    "unusual traffic",
    "our systems have detected",
    "captcha",
    "подтвердите, что запросы отправляли вы",
    "нам очень жаль",
    "доступ к сервису временно ограничен",
)
_SKIP_HOSTS = {
    "google.com", "www.google.com", "google.ru", "www.google.ru",
    "yandex.ru", "www.yandex.ru", "ya.ru", "www.ya.ru",
    "bing.com", "www.bing.com", "duckduckgo.com", "html.duckduckgo.com",
}


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []
        self._href = ""
        self._class = ""
        self._text: list[str] = []
        self._inside_a = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        self._href = values.get("href", "")
        self._class = values.get("class", "")
        self._text = []
        self._inside_a = True

    def handle_data(self, data: str) -> None:
        if self._inside_a and data:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._inside_a:
            return
        text = " ".join(" ".join(self._text).split())
        self.links.append((self._href, text, self._class))
        self._href = ""
        self._class = ""
        self._text = []
        self._inside_a = False


def _unwrap_search_href(engine: str, href: str) -> str:
    value = html.unescape(str(href or "").strip())
    if not value:
        return ""

    if value.startswith("//"):
        value = "https:" + value

    if engine == "google" and value.startswith("/url?"):
        params = parse_qs(urlsplit(value).query)
        value = (params.get("q") or params.get("url") or [""])[0]
    elif engine == "duckduckgo":
        parsed = urlsplit(value)
        if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
            value = unquote((parse_qs(parsed.query).get("uddg") or [""])[0])

    if not value.startswith(("http://", "https://")):
        return ""

    host = (urlsplit(value).hostname or "").casefold().removeprefix("www.")
    if not host:
        return ""
    if host in {item.removeprefix("www.") for item in _SKIP_HOSTS}:
        return ""
    return value


def _looks_like_result(engine: str, text: str, css_class: str, url: str) -> bool:
    if len(text.strip()) < 3 or not url:
        return False
    cls = css_class.casefold()
    if engine == "duckduckgo":
        return "result__a" in cls or "result-link" in cls
    if engine == "bing":
        return "b_algo" in cls or len(text) >= 8
    if engine == "yandex":
        return any(token in cls for token in ("organic", "link", "title")) or len(text) >= 8
    if engine == "google":
        return len(text) >= 8
    return True


def _parse_results(engine: str, query: str, body: str, *, limit: int) -> list[SearchHit]:
    parser = _AnchorParser()
    parser.feed(body)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for href, text, css_class in parser.links:
        url = _unwrap_search_href(engine, href)
        if not _looks_like_result(engine, text, css_class, url):
            continue
        canonical = canonical_result_url(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        title = re.sub(r"\s+", " ", text).strip()[:320]
        hits.append(SearchHit(
            query=query,
            title=title,
            url=url,
            snippet="",
            rank=len(hits) + 1,
            provider=f"free_serp:{engine}",
        ))
        if len(hits) >= limit:
            break
    return hits


class FreeSerpDiscovery:
    """Keyless, low-rate fallback over public search result pages.

    This provider does not bypass CAPTCHA, rotate identities, or evade rate
    limits. If an engine blocks the request it is treated as unavailable and
    the remaining engines are used. Google and Yandex are attempted first;
    DDG and Bing provide independent fallbacks.
    """

    name = "free_serp"

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 8.0))

    @staticmethod
    def _url(engine: str, query: str, language: str | None) -> str:
        q = quote_plus(query)
        lang = "ru" if str(language or "").lower().startswith("ru") else "en"
        if engine == "google":
            return f"https://www.google.com/search?q={q}&num=10&hl={lang}&filter=0"
        if engine == "yandex":
            return f"https://yandex.ru/search/?text={q}&lang={lang}"
        if engine == "duckduckgo":
            return f"https://html.duckduckgo.com/html/?q={q}"
        return f"https://www.bing.com/search?q={q}&count=10&setlang={lang}"

    async def _one(self, engine: str, query: str, language: str | None, limit: int) -> list[SearchHit]:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7" if str(language or "").lower().startswith("ru") else "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(3.0, self.timeout_seconds)),
                follow_redirects=True,
                trust_env=False,
                headers=headers,
            ) as client:
                response = await client.get(self._url(engine, query, language))
                response.raise_for_status()
        except httpx.HTTPError:
            return []

        body = response.text[:1_500_000]
        low = body.casefold()
        if any(marker in low for marker in _BLOCKED_MARKERS):
            return []
        try:
            return _parse_results(engine, query, body, limit=limit)
        except Exception:
            return []

    async def search(self, query: str, *, count: int = 10, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        _ = country
        limit = min(max(int(count), 1), 20)
        engines = ("google", "yandex", "duckduckgo", "bing")
        batches = await asyncio.gather(*(self._one(engine, query, language, limit) for engine in engines))

        # Interleave engines so one noisy source cannot dominate the final list.
        merged: list[SearchHit] = []
        max_len = max((len(batch) for batch in batches), default=0)
        for index in range(max_len):
            for batch in batches:
                if index < len(batch):
                    merged.append(batch[index])
        hits = dedupe_hits(merged, limit=limit)
        if not hits:
            raise DiscoveryError("Free SERP providers returned no usable results")
        return hits
