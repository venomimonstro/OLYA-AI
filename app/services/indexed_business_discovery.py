from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url, dedupe_hits
from app.services.free_serp_discovery import FreeSerpDiscovery
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_CITY: tuple[tuple[re.Pattern[str], tuple[str, str, str]], ...] = (
    (re.compile(r"\bмоскв\w*\b", re.I), ("moscow", "msk", "Москва")),
    (re.compile(r"\b(?:санкт[-\s]?петербург\w*|петербург\w*|спб)\b", re.I), ("spb", "spb", "Санкт-Петербург")),
    (re.compile(r"\bказан\w*\b", re.I), ("kazan", "kazan", "Казань")),
    (re.compile(r"\bекатеринбург\w*\b", re.I), ("ekaterinburg", "ekaterinburg", "Екатеринбург")),
    (re.compile(r"\bновосибирск\w*\b", re.I), ("novosibirsk", "novosibirsk", "Новосибирск")),
    (re.compile(r"\bсамар\w*\b", re.I), ("samara", "samara", "Самара")),
    (re.compile(r"\bчелябинск\w*\b", re.I), ("chelyabinsk", "chelyabinsk", "Челябинск")),
    (re.compile(r"\bкрасноярск\w*\b", re.I), ("krasnoyarsk", "krasnoyarsk", "Красноярск")),
    (re.compile(r"\bтюмен\w*\b", re.I), ("tyumen", "tyumen", "Тюмень")),
    (re.compile(r"\bуф\w*\b", re.I), ("ufa", "ufa", "Уфа")),
    (re.compile(r"\bперм\w*\b", re.I), ("perm", "perm", "Пермь")),
    (re.compile(r"\bсочи\b", re.I), ("sochi", "sochi", "Сочи")),
    (re.compile(r"\bкраснодар\w*\b", re.I), ("krasnodar", "krasnodar", "Краснодар")),
    (re.compile(r"\bворонеж\w*\b", re.I), ("voronezh", "voronezh", "Воронеж")),
    (re.compile(r"\bомск\w*\b", re.I), ("omsk", "omsk", "Омск")),
)

_RATING_RE = re.compile(
    r"(?:рейтинг\s*[:\-]?\s*([1-5](?:[.,]\d{1,2})?)|([1-5][.,]\d{1,2})\s*(?:из\s*5|★|звезд))",
    re.I,
)
_REVIEWS_RE = re.compile(r"(\d[\d\s]{0,7})\s+(?:отзыв\w*|оцен\w*)", re.I)
_GENERIC_RE = re.compile(
    r"\s*(?:[|—–-]\s*)?(?:2гис|2gis|yell|zoon|зун|отзывы|цены|адрес|телефон|официальный сайт).*$",
    re.I,
)
_FREE_SERP = FreeSerpDiscovery(timeout_seconds=3.8)


def _city(question: str) -> tuple[str, str, str]:
    text = normalized_question(question)
    for pattern, value in _CITY:
        if pattern.search(text):
            return value
    return "moscow", "msk", "Москва"


def _clean_name(hit: SearchHit) -> str:
    value = " ".join(str(hit.title or "").split()).strip(" -–—|,:.")
    value = _GENERIC_RE.sub("", value).strip(" -–—|,:.")
    if "," in value:
        left = value.split(",", 1)[0].strip()
        if len(left) >= 2:
            value = left
    return value[:140]


def _metrics(hit: SearchHit) -> tuple[float | None, int | None]:
    text = " ".join((str(hit.title or ""), str(hit.snippet or "")))
    rating = None
    reviews = None
    match = _RATING_RE.search(text)
    if match:
        raw = match.group(1) or match.group(2) or ""
        try:
            candidate = float(raw.replace(",", "."))
            if 0 < candidate <= 5.1:
                rating = round(candidate, 2)
        except ValueError:
            pass
    match = _REVIEWS_RE.search(text)
    if match:
        try:
            reviews = int(match.group(1).replace(" ", ""))
        except ValueError:
            pass
    return rating, reviews


def _provider_for(url: str, gis_city: str, zoon_city: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = (parsed.path or "").casefold()
    if host == "2gis.ru" and f"/{gis_city}/firm/" in path:
        return "2gis"
    if host == "yell.ru" and f"/{gis_city}/com/" in path:
        return "yell"
    if host == "zoon.ru" and path.startswith(f"/{zoon_city}/"):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3:
            return "zoon"
    return ""


def _queries(question: str) -> tuple[str, ...]:
    gis_city, zoon_city, _city_name = _city(question)
    clean = " ".join(str(question or "").split())
    return (
        f"site:2gis.ru/{gis_city}/firm/ {clean}",
        f"site:yell.ru/{gis_city}/com/ {clean}",
        f"site:zoon.ru/{zoon_city}/ {clean}",
    )


async def discover_indexed_businesses(question: str, discovery, *, limit: int = 10) -> list[MapPlace]:
    """Find concrete business cards via public search indexes.

    Direct directory pages are frequently JS-heavy or VPS-blocked. Concrete
    card URLs already present in Yandex/DDG/Bing indexes are still useful
    evidence. Google is intentionally not used in this Russian fallback.
    """
    gis_city, zoon_city, _city_name = _city(question)
    per_query = max(12, limit * 2)

    async def one(query: str) -> list[SearchHit]:
        async def primary() -> list[SearchHit]:
            try:
                return await asyncio.wait_for(
                    discovery.search(query, count=per_query, country="RU", language="ru"),
                    timeout=4.2,
                )
            except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
                return []

        # Run the configured metasearch and three direct keyless SERPs in
        # parallel. FreeSerpDiscovery._one is intentionally bounded and fails
        # closed on CAPTCHA/HTTP errors; no evasion or proxy rotation is used.
        batches = await asyncio.gather(
            primary(),
            _FREE_SERP._one("yandex", query, "ru", per_query),
            _FREE_SERP._one("duckduckgo", query, "ru", per_query),
            _FREE_SERP._one("bing", query, "ru", per_query),
        )
        merged: list[SearchHit] = []
        max_len = max((len(batch) for batch in batches), default=0)
        for index in range(max_len):
            for batch in batches:
                if index < len(batch):
                    merged.append(batch[index])
        return dedupe_hits(merged, limit=per_query)

    batches = await asyncio.gather(*(one(query) for query in _queries(question)))
    rows: list[MapPlace] = []
    seen: set[str] = set()
    for batch in batches:
        for hit in batch:
            provider = _provider_for(hit.url, gis_city, zoon_city)
            if not provider:
                continue
            key = canonical_result_url(hit.url)
            if not key or key in seen:
                continue
            name = _clean_name(hit)
            if len(name) < 2:
                continue
            seen.add(key)
            rating, reviews = _metrics(hit)
            rows.append(MapPlace(
                provider=provider,
                name=name,
                card_url=str(hit.url),
                rating=rating,
                reviews=reviews,
                source_url=str(hit.url),
            ))
            if len(rows) >= limit:
                return rows
    return rows
