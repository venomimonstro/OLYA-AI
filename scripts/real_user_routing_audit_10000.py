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
    return _actual_prompt_path(case.prompt)


def _actual_prompt_path(prompt: str) -> tuple[str, dict]:
    utility = utility_reply(prompt)
    if utility is not None:
        return "instant", {"utility_kind": utility.kind, "utility_text": utility.text}
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


_EDGE_CASES: tuple[tuple[str, str, str | None], ...] = (
    ("Подскажи, пожалуйста, сколько будет 17 * 23? 🙏", "instant", "calculator"),
    ("Где поесть рядом?", "instant", "location_clarification"),
    ("Куда сходить сегодня?", "instant", "location_clarification"),
    ("Что посмотреть рядом?", "instant", "location_clarification"),
    ("Где поесть рядом с Красной площадью в Москве?", "web", None),
    ("Куда сходить сегодня в Москве?", "web", None),
    ("100 долларов в рублях", "structured", None),
    ("10 000 рублей в долларах", "structured", None),
    ("$100 в рублях", "structured", None),
    ("100€ в рублях", "structured", None),
    ("10 000 ₽ в долларах", "structured", None),
    ("Как работает УСН?", "web", None),
    ("Какие налоги платит самозанятый в России?", "web", None),
    ("Какие основные требования трудового законодательства к дистанционной работе?", "web", None),
    ("Какие правила въезда россиян в Японию?", "web", None),
    ("Что такое НДС?", "atomic", None),
    ("Объясни разницу между компетенциями, навыками и требованиями вакансии.", "direct", None),
    ("Найди вакансии Python-разработчика в Москве.", "web", None),
    ("Актуальные вакансии маркетолога удалённо", "web", None),
    ("Перепиши профессиональнее: Наши лучшие рекомендации помогут улучшить рейтинг и отзывы клиентов.", "direct", None),
    ("Посоветуй фильм на вечер без тяжёлой драмы.", "direct", None),
)


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

    edge_results: list[dict] = []
    edge_failed = 0
    for prompt, expected, expected_utility_kind in _EDGE_CASES:
        actual, details = _actual_prompt_path(prompt)
        utility_ok = expected_utility_kind is None or details.get("utility_kind") == expected_utility_kind
        ok = actual == expected and utility_ok
        if not ok:
            edge_failed += 1
        edge_results.append({
            "prompt": prompt,
            "expected": expected,
            "actual": actual,
            "expected_utility_kind": expected_utility_kind,
            "details": details,
            "ok": ok,
        })

    failed = sum(persona_fail.values())
    passed = len(POPULATION) - failed
    return {
        "format": "olya-real-user-routing-10000-v5",
        "status": "passed" if failed == 0 and edge_failed == 0 else "failed",
        "population": len(POPULATION),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / len(POPULATION), 5),
        "edge_tests": len(edge_results),
        "edge_failed": edge_failed,
        "edge_results": edge_results,
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
