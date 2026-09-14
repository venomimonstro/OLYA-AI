from __future__ import annotations

import re
from urllib.parse import urlsplit


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


_PRIMARY_HOSTS = {
    "cbr.ru",
    "government.ru",
    "kremlin.ru",
    "publication.pravo.gov.ru",
    "pravo.gov.ru",
    "whitehouse.gov",
    "congress.gov",
    "who.int",
    "ecb.europa.eu",
    "federalreserve.gov",
    "python.org",
    "docs.python.org",
    "pypi.org",
    "ietf.org",
    "rfc-editor.org",
    "w3.org",
}

_FAST_CHANGING = re.compile(
    r"\b(?:курс|usd|eur|cny|rub|btc|bitcoin|ethereum|eth|погода|weather|forecast|"
    r"котиров|stock|price|цена|сейчас|today|current|latest)\b",
    re.IGNORECASE,
)
_SOFTWARE = re.compile(r"\b(?:version|версия|release|релиз|python|node|react|postgres|docker|nginx|php)\b", re.IGNORECASE)


def _is_primary(host: str) -> bool:
    if not host:
        return False
    if host in _PRIMARY_HOSTS:
        return True
    if host.endswith(".gov") or host.endswith(".gov.ru"):
        return True
    if host.endswith(".go.jp") or host.endswith(".gov.uk"):
        return True
    return False


def _looks_like_docs(host: str, title: str, snippet: str) -> bool:
    text = f"{title} {snippet}".casefold()
    return (
        host.startswith("docs.")
        or "documentation" in text
        or "документац" in text
        or "reference" in text
        or "справочник" in text
    )


def _cache_ttl(query: str, requested: int) -> int:
    """Keep fast-changing search fallbacks fresh without wasting repeat queries."""
    value = " ".join(str(query or "").split())
    if _FAST_CHANGING.search(value):
        # Structured resolvers normally handle FX/crypto/weather first. This TTL
        # protects the generic web fallback when a direct provider is unavailable.
        return min(int(requested), 90)
    if _SOFTWARE.search(value):
        return min(int(requested), 15 * 60)
    return min(max(int(requested), 60), 6 * 60 * 60)


def install_search_quality_patch() -> None:
    """Authority-aware ranking plus freshness-aware search caching."""
    from app.services import discovery as discovery
    from app.services import task_solver as task_solver

    if getattr(discovery.enrich_hit, "_olya_authority_ranking", False):
        return

    base_classify = discovery.classify_source
    base_cached_search = discovery.cached_provider_search

    def classify_source(url: str, title: str = "", snippet: str = "") -> tuple[str, float]:
        host = _host(url)
        if _is_primary(host):
            return "primary_official", 0.97
        if _looks_like_docs(host, title, snippet):
            return "documentation", 0.91
        return base_classify(url, title, snippet)

    def enrich_hit(hit):
        source_kind, base_score = classify_source(hit.url, hit.title, hit.snippet)
        rank_bonus = max(0.0, (11 - min(int(hit.rank or 10), 10)) / 120)
        # Agreement between several search engines is a useful discovery signal,
        # but it never outranks primary-source authority by itself.
        provider_bonus = 0.015 if "," in str(hit.provider or "") else 0.0
        return {
            "query": hit.query,
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
            "rank": hit.rank,
            "provider": hit.provider,
            "source_kind": source_kind,
            "discovery_score": round(min(base_score + rank_bonus + provider_bonus, 0.995), 3),
        }

    async def cached_provider_search(
        db, discovery_obj, query: str, *, count: int, country: str | None,
        language: str | None, ttl_seconds: int = 3600, quality_mode: bool = False,
    ):
        return await base_cached_search(
            db,
            discovery_obj,
            query,
            count=count,
            country=country,
            language=language,
            ttl_seconds=_cache_ttl(query, ttl_seconds),
            quality_mode=quality_mode,
        )

    def diversify_hits(hits, *, kind: str, limit: int):
        enriched = [enrich_hit(hit) for hit in discovery.dedupe_hits(hits, limit=max(limit * 5, limit))]
        priority = {
            "primary_official": 7,
            "documentation": 6,
            "official_candidate": 5,
            "maps_catalog": 4,
            "reviews": 3,
            "web": 2,
        }
        enriched.sort(
            key=lambda row: (
                priority.get(str(row.get("source_kind")), 1),
                float(row.get("discovery_score") or 0),
                -int(row.get("rank") or 999),
            ),
            reverse=True,
        )
        result = []
        seen_urls: set[str] = set()
        per_host: dict[str, int] = {}

        preferred = (
            ("primary_official", "official_candidate", "maps_catalog", "reviews", "web")
            if kind == "local_recommendation"
            else ("primary_official", "documentation")
        )
        for wanted in preferred:
            row = next(
                (
                    item for item in enriched
                    if item.get("source_kind") == wanted
                    and discovery.canonical_result_url(str(item.get("url") or "")) not in seen_urls
                ),
                None,
            )
            if row is None:
                continue
            url = discovery.canonical_result_url(str(row.get("url") or ""))
            host = _host(url)
            if host and per_host.get(host, 0) >= 1:
                continue
            result.append(row)
            seen_urls.add(url)
            per_host[host] = per_host.get(host, 0) + 1
            if len(result) >= limit:
                return result

        for row in enriched:
            url = discovery.canonical_result_url(str(row.get("url") or ""))
            if not url or url in seen_urls:
                continue
            host = _host(url)
            max_host = 2 if kind == "website_audit" else 1
            if host and per_host.get(host, 0) >= max_host:
                continue
            result.append(row)
            seen_urls.add(url)
            per_host[host] = per_host.get(host, 0) + 1
            if len(result) >= limit:
                break
        return result

    classify_source._olya_authority_ranking = True  # type: ignore[attr-defined]
    enrich_hit._olya_authority_ranking = True  # type: ignore[attr-defined]
    cached_provider_search._olya_fresh_cache = True  # type: ignore[attr-defined]
    diversify_hits._olya_authority_ranking = True  # type: ignore[attr-defined]
    discovery.classify_source = classify_source
    discovery.enrich_hit = enrich_hit
    discovery.cached_provider_search = cached_provider_search
    task_solver.enrich_hit = enrich_hit
    task_solver.cached_provider_search = cached_provider_search
    task_solver.diversify_hits = diversify_hits
