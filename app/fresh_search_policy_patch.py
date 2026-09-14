from __future__ import annotations

from app.services.freshness import classify_freshness


def install_fresh_search_policy_patch() -> None:
    # fast_web_grounding imports cached_provider_search directly, so patch that
    # module reference before smart_chat binds the execution path.
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
            # Current office holders are resolved live every time: neither model
            # memory nor an older cached SERP may decide who occupies a role now.
            if decision.category == "official_role":
                ttl_seconds = 0
            else:
                # Other changing facts get a short cache to protect free engines
                # from bursts while remaining substantially fresher than 1 hour.
                ttl_seconds = min(max(0, int(ttl_seconds)), 300)
            quality_mode = True
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
