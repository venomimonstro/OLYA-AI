from __future__ import annotations

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
        or "/docs" in text
        or "documentation" in text
        or "документац" in text
        or "reference" in text
        or "справочник" in text
    )


def install_search_quality_patch() -> None:
    """Prefer primary evidence without making ordinary search slower.

    SearXNG already fans one query out to several engines. The missing quality
    layer was authority-aware source ordering: an official regulator or project
    documentation should beat an SEO article merely because the article ranked
    one position higher in a SERP. This patch keeps source diversity while
    explicitly preferring primary sources and technical documentation.
    """
    from app.services import discovery as discovery
    from app.services import task_solver as task_solver

    if getattr(discovery.enrich_hit, "_olya_authority_ranking", False):
        return

    base_classify = discovery.classify_source

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

        # Recommendations deliberately preserve heterogeneous evidence. Factual
        # questions instead naturally start with the strongest primary source.
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
    diversify_hits._olya_authority_ranking = True  # type: ignore[attr-defined]
    discovery.classify_source = classify_source
    discovery.enrich_hit = enrich_hit
    task_solver.enrich_hit = enrich_hit
    task_solver.diversify_hits = diversify_hits
