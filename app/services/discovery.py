from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SearchQueryCache, SearchProviderStat, utcnow


class DiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    query: str
    title: str
    url: str
    snippet: str
    rank: int
    provider: str


@dataclass(frozen=True)
class ProviderSearchOutcome:
    provider: str
    hits: list[SearchHit]
    latency_ms: int
    error: str = ""
    cached: bool = False


def canonical_result_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = parsed.scheme.lower() or "https"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    tracking = {"gclid", "yclid", "fbclid", "ref", "referrer"}
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        low = key.casefold()
        if low.startswith("utm_") or low in tracking:
            continue
        query_items.append((key, value))
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, host, path, query, ""))


def dedupe_hits(hits: list[SearchHit], *, limit: int) -> list[SearchHit]:
    seen: set[str] = set()
    result: list[SearchHit] = []
    for hit in hits:
        key = canonical_result_url(hit.url)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(hit)
        if len(result) >= limit:
            break
    return result


class BraveSearchDiscovery:
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, timeout_seconds: float = 10.0) -> None:
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, *, count: int = 10, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        if not self.api_key:
            raise DiscoveryError("Search discovery is not configured")
        params: dict[str, object] = {"q": query, "count": min(max(count, 1), 20), "safesearch": "moderate"}
        if country:
            params["country"] = country
        if language:
            params["search_lang"] = language
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.get(self.endpoint, params=params, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DiscoveryError("Search provider request failed") from exc
        try:
            payload = response.json()
            rows = payload.get("web", {}).get("results", [])
        except Exception as exc:
            raise DiscoveryError("Search provider returned an invalid response") from exc
        hits: list[SearchHit] = []
        for index, row in enumerate(rows, start=1):
            url = str(row.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            hits.append(SearchHit(query=query, title=str(row.get("title") or "")[:500], url=url,
                                  snippet=str(row.get("description") or "")[:2000], rank=index, provider=self.name))
        return hits


class DisabledDiscovery:
    name = "disabled"
    async def search(self, query: str, *, count: int = 10, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        _ = query, count, country, language
        raise DiscoveryError("Search discovery is disabled; configure a search provider")


class ProviderPoolDiscovery:
    """Runs ordinary search providers with economical failover or quality aggregation."""
    def __init__(self, providers: list[object]) -> None:
        self.providers = [p for p in providers if getattr(p, "name", "disabled") != "disabled"]

    async def search_outcomes(self, query: str, *, count: int = 10, country: str | None = None,
                              language: str | None = None, use_all: bool = True) -> list[ProviderSearchOutcome]:
        if not self.providers:
            raise DiscoveryError("Search discovery is disabled; configure a search provider")

        async def one(provider: object) -> ProviderSearchOutcome:
            start = time.perf_counter()
            try:
                hits = await provider.search(query, count=count, country=country, language=language)
                return ProviderSearchOutcome(getattr(provider, "name", provider.__class__.__name__), hits,
                                             int((time.perf_counter() - start) * 1000))
            except DiscoveryError as exc:
                return ProviderSearchOutcome(getattr(provider, "name", provider.__class__.__name__), [],
                                             int((time.perf_counter() - start) * 1000), str(exc))

        if use_all:
            return list(await asyncio.gather(*(one(p) for p in self.providers)))
        outcomes: list[ProviderSearchOutcome] = []
        for provider in self.providers:
            outcome = await one(provider)
            outcomes.append(outcome)
            if not outcome.error and outcome.hits:
                break
        return outcomes

    async def search(self, query: str, *, count: int = 10, country: str | None = None, language: str | None = None) -> list[SearchHit]:
        outcomes = await self.search_outcomes(query, count=count, country=country, language=language, use_all=False)
        successful = [o for o in outcomes if not o.error]
        if not successful:
            raise DiscoveryError("All configured search providers failed")
        merged = [hit for outcome in successful for hit in outcome.hits]
        return dedupe_hits(merged, limit=count)


def provider_health_score(stat: SearchProviderStat | None) -> float:
    if stat is None or not stat.request_count:
        return 1.0
    success_ratio = float(stat.success_count or 0) / max(int(stat.request_count or 0), 1)
    latency_penalty = min(float(stat.last_latency_ms or 0) / 10_000.0, 0.35)
    return round(max(0.0, success_ratio - latency_penalty), 4)


def _cache_key(query: str, country: str | None, language: str | None, provider_set: str, count: int, quality_mode: bool) -> str:
    raw = json.dumps([" ".join(query.split()).casefold(), country or "", language or "", provider_set, int(count), bool(quality_mode)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_hits(hits: list[SearchHit]) -> list[dict]:
    return [h.__dict__ for h in hits]


def _deserialize_hits(rows: list[dict]) -> list[SearchHit]:
    return [SearchHit(**row) for row in rows]


async def cached_provider_search(db: Session, discovery: object, query: str, *, count: int, country: str | None,
                                 language: str | None, ttl_seconds: int = 3600, quality_mode: bool = False) -> list[SearchHit]:
    provider_names = ",".join(sorted(getattr(p, "name", "unknown") for p in getattr(discovery, "providers", [discovery])))
    key = _cache_key(query, country, language, provider_names, count, quality_mode)
    row = db.scalar(select(SearchQueryCache).where(SearchQueryCache.cache_key == key).limit(1))
    now = utcnow()
    if row is not None:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        age = (now - created).total_seconds()
        if age <= ttl_seconds:
            row.hit_count += 1
            db.flush()
            return _deserialize_hits(list(row.results or []))[:count]

    start = time.perf_counter()
    if isinstance(discovery, ProviderPoolDiscovery):
        ordered = sorted(discovery.providers, key=lambda p: provider_health_score(db.get(SearchProviderStat, getattr(p, "name", ""))), reverse=True)
        active = []
        for provider in ordered:
            stat = db.get(SearchProviderStat, getattr(provider, "name", ""))
            if stat is not None and int(stat.request_count or 0) >= 5 and provider_health_score(stat) < 0.20:
                continue
            active.append(provider)
        if not active:
            active = ordered
        runtime_pool = ProviderPoolDiscovery(active)
        outcomes = await runtime_pool.search_outcomes(query, count=count, country=country, language=language, use_all=quality_mode)
        good = [o for o in outcomes if not o.error]
        for outcome in outcomes:
            stat = db.get(SearchProviderStat, outcome.provider)
            if stat is None:
                stat = SearchProviderStat(provider=outcome.provider, request_count=0, success_count=0, failure_count=0, total_latency_ms=0, last_latency_ms=0, last_error="")
                db.add(stat)
            stat.request_count = int(stat.request_count or 0) + 1
            stat.last_latency_ms = outcome.latency_ms
            stat.total_latency_ms = int(stat.total_latency_ms or 0) + outcome.latency_ms
            stat.last_checked_at = now
            if outcome.error:
                stat.failure_count = int(stat.failure_count or 0) + 1
                stat.last_error = outcome.error[:500]
            else:
                stat.success_count = int(stat.success_count or 0) + 1
                stat.last_error = ""
        if not good:
            db.flush()
            raise DiscoveryError("All configured search providers failed")
        merged: list[SearchHit] = []
        max_len = max((len(o.hits) for o in good), default=0)
        for idx in range(max_len):
            for outcome in good:
                if idx < len(outcome.hits):
                    merged.append(outcome.hits[idx])
        hits = dedupe_hits(merged, limit=count)
    else:
        hits = await discovery.search(query, count=count, country=country, language=language)
    elapsed = int((time.perf_counter() - start) * 1000)
    if row is None:
        row = SearchQueryCache(cache_key=key, query=query, country=country or "", language=language or "",
                               provider_set=provider_names, results=_serialize_hits(hits), latency_ms=elapsed)
        db.add(row)
    else:
        row.results = _serialize_hits(hits)
        row.created_at = now
        row.latency_ms = elapsed
    db.flush()
    return hits


_MAP_CATALOG_HOSTS = ("yandex.ru", "2gis.ru", "zoon.ru", "prodoctorov.ru")
_REVIEW_HOSTS = ("otzovik.com", "irecommend.ru", "prodoctorov.ru", "yell.ru")


def classify_source(url: str, title: str = "", snippet: str = "") -> tuple[str, float]:
    host = (urlsplit(url).hostname or "").lower()
    text = f"{title} {snippet}".casefold()
    if any(host == item or host.endswith(f".{item}") for item in _MAP_CATALOG_HOSTS):
        return "maps_catalog", 0.82
    if any(host == item or host.endswith(f".{item}") for item in _REVIEW_HOSTS) or "отзыв" in text:
        return "reviews", 0.72
    if any(word in text for word in ("официальный сайт", "официальный")):
        return "official_candidate", 0.88
    return "web", 0.60


def enrich_hit(hit: SearchHit) -> dict:
    source_kind, base_score = classify_source(hit.url, hit.title, hit.snippet)
    rank_bonus = max(0.0, (11 - min(hit.rank, 10)) / 100)
    return {"query": hit.query, "title": hit.title, "url": hit.url, "snippet": hit.snippet, "rank": hit.rank,
            "provider": hit.provider, "source_kind": source_kind,
            "discovery_score": round(min(base_score + rank_bonus, 0.99), 3)}
