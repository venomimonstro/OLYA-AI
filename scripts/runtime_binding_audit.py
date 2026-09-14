#!/usr/bin/env python3
from __future__ import annotations

import json

# Import the full ASGI application, not only the bootstrap package. This forces
# production route modules to bind their direct imports exactly as Uvicorn does.
import app.main  # noqa: F401
from app.api.routes import smart_chat
from app.services import discovery, fast_web_grounding, freshness, structured_facts, task_solver


def audit() -> dict:
    checks = {
        "search_cache_patch_installed": bool(getattr(discovery.cached_provider_search, "_olya_fresh_cache", False)),
        "authority_ranking_installed": bool(getattr(task_solver.diversify_hits, "_olya_authority_ranking", False)),
        "answer_intent_web_installed": bool(getattr(fast_web_grounding.should_auto_ground, "_olya_intent_web", False)),
        "compact_web_synthesis_installed": bool(getattr(fast_web_grounding.execute_fast_web_grounding, "_olya_compact_synthesis", False)),
        "fast_grounding_cache_binding_current": fast_web_grounding.cached_provider_search is discovery.cached_provider_search,
        "fast_grounding_rank_binding_current": fast_web_grounding.diversify_hits is task_solver.diversify_hits,
        "smart_chat_web_binding_current": smart_chat.execute_fast_web_grounding is fast_web_grounding.execute_fast_web_grounding,
        "smart_chat_auto_ground_binding_current": smart_chat.should_auto_ground is fast_web_grounding.should_auto_ground,
        "smart_chat_structured_resolver_current": smart_chat.resolve_structured_fact is structured_facts.resolve_structured_fact,
        "smart_chat_structured_detector_current": smart_chat.is_currency_rate_question is structured_facts.is_currency_rate_question,
        "smart_chat_freshness_binding_current": smart_chat.classify_freshness is freshness.classify_freshness,
        "fast_grounding_freshness_binding_current": fast_web_grounding.classify_freshness is freshness.classify_freshness,
        "live_structured_patch_installed": bool(getattr(structured_facts.resolve_structured_fact, "_olya_live_structured", False)),
    }
    errors = [key for key, value in checks.items() if not value]
    result = {
        "format": "olya-runtime-binding-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
    }
    return result


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
