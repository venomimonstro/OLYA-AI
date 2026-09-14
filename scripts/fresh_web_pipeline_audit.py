from __future__ import annotations

import json

from app.services.freshness import classify_freshness
from app.services.task_solver import TaskExecution, TaskSolvePlan
from app.ultrafast_fresh_web_patch import promote_search_evidence


def audit() -> dict:
    errors: list[str] = []
    question = "какой курс доллар рубль сейчас?"
    freshness = classify_freshness(question)
    if not freshness.required or freshness.category != "market":
        errors.append(f"currency query classified as {freshness.category!r}, required={freshness.required}")

    execution = TaskExecution(
        plan=TaskSolvePlan(
            kind="web_research",
            requires_web=True,
            queries=(question,),
            freshness_category="market",
            force_freshness=True,
        ),
        public_sources=[
            {
                "title": "USD RUB exchange rate today",
                "url": "https://example-finance-a.test/usd-rub",
                "snippet": "Current USD/RUB exchange rate is shown here with today's market value.",
                "verified": False,
            },
            {
                "title": "Dollar to Russian ruble rate",
                "url": "https://example-finance-b.test/rates/usd-rub",
                "snippet": "Live dollar to ruble quotation and current exchange-rate information.",
                "verified": False,
            },
        ],
        fetched_sources=0,
        independent_hosts=2,
        warnings=["Страницы источников не загрузились; подтверждённого свежего факта пока нет"],
    )
    promote_search_evidence(execution, question)
    evidence = int(getattr(execution, "fresh_evidence_count", 0) or 0)
    confirmed = int(getattr(execution, "search_confirmed_sources", 0) or 0)
    if evidence < 2:
        errors.append(f"search fallback evidence={evidence}, expected >=2")
    if confirmed < 2:
        errors.append(f"search-confirmed sources={confirmed}, expected >=2")
    if not all(row.get("search_confirmed") is True for row in execution.public_sources):
        errors.append("independent fresh search rows were not promoted to evidence")
    if not any("FRESH SEARCH CONSENSUS" in message.content for message in execution.context_messages):
        errors.append("fresh search consensus was not injected into LLM context")
    if any("подтверждённого свежего факта пока нет" in warning for warning in execution.warnings):
        errors.append("obsolete no-evidence warning survived promotion")

    result = {
        "format": "olya-fresh-web-pipeline-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "currency_freshness": {
            "required": freshness.required,
            "category": freshness.category,
            "min_independent_hosts": freshness.min_independent_hosts,
        },
        "search_fallback": {
            "fresh_evidence_count": evidence,
            "search_confirmed_sources": confirmed,
            "llm_context_injected": any("FRESH SEARCH CONSENSUS" in message.content for message in execution.context_messages),
        },
        "latency_policy": {
            "search_engine_timeout_seconds": 2.5,
            "search_engine_max_timeout_seconds": 4.0,
            "page_fetch_timeout_seconds": 2.5,
            "fresh_search_cache_seconds_market": 60,
        },
    }
    return result


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
