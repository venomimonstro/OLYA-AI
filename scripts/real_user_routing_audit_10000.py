#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter, defaultdict
import json

from app.inference.router import choose_route
from app.services.clean_web import should_use_web
from app.services.live_structured_facts import is_live_structured_question
from app.services.response_strategy import (
    is_atomic_knowledge_question,
    requires_conversation_context,
    requires_memory_context,
)
from app.utility_chat import utility_reply
from scripts.real_user_population_10000 import POPULATION, SyntheticSession


def _actual_path(case: SyntheticSession) -> tuple[str, dict]:
    prompt = case.prompt
    utility = utility_reply(prompt)
    if utility is not None:
        return "instant", {"utility_kind": utility.kind}
    if is_live_structured_question(prompt):
        return "structured", {}
    if requires_memory_context(prompt):
        return "memory", {}
    if requires_conversation_context(prompt):
        return "context", {}
    route = choose_route(prompt, "auto", 4096, 4096)
    if route.max_output_tokens >= 2500 and "long_form" in route.reason:
        return "longform", {"max_output_tokens": route.max_output_tokens, "reason": route.reason}
    if should_use_web(prompt, "auto"):
        return "web", {"mode": route.mode, "reason": route.reason}
    if is_atomic_knowledge_question(prompt):
        return "atomic", {"max_output_tokens": route.max_output_tokens, "reason": route.reason}
    return "direct", {"mode": route.mode, "max_output_tokens": route.max_output_tokens, "reason": route.reason}


def audit() -> dict:
    failures: list[dict] = []
    actual_counts: Counter[str] = Counter()
    expected_counts: Counter[str] = Counter()
    persona_fail: Counter[str] = Counter()
    style_fail: Counter[str] = Counter()
    category_fail: Counter[str] = Counter()
    matrix: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for case in POPULATION:
        actual, details = _actual_path(case)
        expected = case.expected_path
        actual_counts[actual] += 1
        expected_counts[expected] += 1
        matrix[expected][actual] += 1
        if actual != expected:
            persona_fail[case.persona] += 1
            style_fail[case.style] += 1
            category_fail[case.category] += 1
            if len(failures) < 120:
                failures.append({
                    "id": case.id,
                    "base_id": case.base_id,
                    "persona": case.persona_label,
                    "style": case.style,
                    "category": case.category,
                    "prompt": case.prompt,
                    "expected": expected,
                    "actual": actual,
                    "details": details,
                })

    failed = sum(persona_fail.values())
    passed = len(POPULATION) - failed
    return {
        "format": "olya-real-user-routing-10000-v1",
        "status": "passed" if failed == 0 else "failed",
        "population": len(POPULATION),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / len(POPULATION), 5),
        "expected_paths": dict(expected_counts),
        "actual_paths": dict(actual_counts),
        "route_matrix": {key: dict(value) for key, value in matrix.items()},
        "failures_by_persona": dict(persona_fail.most_common()),
        "failures_by_style": dict(style_fail.most_common()),
        "failures_by_category": dict(category_fail.most_common()),
        "failure_samples": failures,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
