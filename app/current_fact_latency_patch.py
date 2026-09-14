from __future__ import annotations


_CONCISE_FRESH_CATEGORIES = {
    "official_role": 160,
    "weather": 220,
    "market": 220,
    "price": 220,
    "schedule": 220,
    "software_version": 220,
    "availability": 220,
}
_ANALYTIC_MARKERS = (
    "подробно", "детально", "проанализ", "сравни", "объясни почему", "истори", "биограф",
    "deep research", "detailed", "analyze", "compare",
)


def _is_concise_fresh_lookup(text: str, requested_mode: str) -> tuple[bool, int]:
    _ = requested_mode
    normalized = " ".join(str(text or "").split()).casefold()
    if not normalized or len(normalized) > 260:
        return False, 0
    if any(marker in normalized for marker in _ANALYTIC_MARKERS):
        return False, 0
    from app.services.freshness import classify_freshness
    decision = classify_freshness(normalized)
    cap = _CONCISE_FRESH_CATEGORIES.get(decision.category)
    return bool(decision.required and cap), int(cap or 0)


def install_current_fact_latency_patch() -> None:
    from app.inference import router as inference_router
    current = inference_router.choose_route
    if getattr(current, "_olya_current_fact_latency", False):
        return

    def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int):
        decision = current(text, requested_mode, normal_context, deep_context)
        concise, cap = _is_concise_fresh_lookup(text, requested_mode)
        if not concise:
            return decision
        # Quality level controls complex reasoning, not the cost of retrieving an
        # already-verifiable atomic fact. Even High uses the fast synthesis lane
        # here; asking a 4B CPU model to "think harder" about an official rate or
        # office holder only adds latency and hallucination risk.
        return inference_router.RouteDecision(
            mode="fast",
            max_context_tokens=decision.max_context_tokens,
            max_output_tokens=min(int(decision.max_output_tokens), cap),
            reasoning=False,
            complexity_score=decision.complexity_score,
            reason=(decision.reason + ",concise_fresh_fast_path").strip(","),
        )

    choose_route._olya_current_fact_latency = True  # type: ignore[attr-defined]
    inference_router.choose_route = choose_route
