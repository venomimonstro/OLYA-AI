from __future__ import annotations

from app.services.freshness import classify_freshness


def install_fresh_search_policy_patch() -> None:
    """Keep changing facts fresh without multiplying provider latency.

    The primary local search provider already aggregates several engines. Asking
    ProviderPool to fan out again in quality mode duplicates work and lets one
    slow secondary provider hold the whole answer. Fresh queries therefore use
    the normal provider path with a very short cache.
    """
    from app.services import fast_web_grounding

    current = fast_web_grounding.cached_provider_search
    if getattr(current, "_olya_fresh_search_policy", False):
        return

    async def guarded(
        db,
        discovery,
        query: str,
        *,
        count: int,
        country: str | None,
        language: str | None,
        ttl_seconds: int = 3600,
        quality_mode: bool = False,
    ):
        decision = classify_freshness(query)
        if decision.required:
            # Office holders must be live. Fast-changing market/weather values
            # get at most a one-minute cache; other current facts at most 3 min.
            if decision.category == "official_role":
                ttl_seconds = 0
            elif decision.category in {"market", "weather", "availability", "schedule"}:
                ttl_seconds = min(max(0, int(ttl_seconds)), 60)
            else:
                ttl_seconds = min(max(0, int(ttl_seconds)), 180)
            # SearXNG already fans one query out to independent web engines.
            quality_mode = False
        return await current(
            db,
            discovery,
            query,
            count=count,
            country=country,
            language=language,
            ttl_seconds=ttl_seconds,
            quality_mode=quality_mode,
        )

    guarded._olya_fresh_search_policy = True  # type: ignore[attr-defined]
    fast_web_grounding.cached_provider_search = guarded
