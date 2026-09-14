from __future__ import annotations


def install_work_quality_floor_patch() -> None:
    """Prevent hidden legacy Fast routing for real user questions.

    Deterministic zero-inference utility shortcuts may keep their internal Fast
    accounting label. Any request that would otherwise use the old
    simple_short_auto_fast model lane is upgraded to non-thinking Work so the
    UI's "Простая" choice means economical normal quality, not a 2B-era answer
    budget.
    """
    from app.inference import router as inference_router

    current = inference_router.choose_route
    if getattr(current, "_olya_work_quality_floor", False):
        return

    def choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int):
        decision = current(text, requested_mode, normal_context, deep_context)
        if decision.mode != "fast" or str(decision.reason).startswith("utility_"):
            return decision
        if str(decision.reason) != "simple_short_auto_fast":
            return decision
        starter_4k = int(deep_context) <= 4096
        return inference_router.RouteDecision(
            mode="work",
            max_context_tokens=min(max(1024, int(normal_context)), max(1024, int(deep_context))),
            max_output_tokens=600 if starter_4k else 1100,
            reasoning=False,
            complexity_score=decision.complexity_score,
            reason="work_quality_floor",
        )

    choose_route._olya_work_quality_floor = True  # type: ignore[attr-defined]
    inference_router.choose_route = choose_route
