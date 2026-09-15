#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict

from app.inference.router import choose_route
from app.services.clean_web import should_use_web
from app.services.live_structured_facts import is_live_structured_question
from app.services.response_strategy import (
    is_atomic_knowledge_question,
    requires_conversation_context,
    requires_memory_context,
)
from app.utility_chat import utility_reply
from scripts.real_user_scenarios import SCENARIOS, UserScenario


def classify(case: UserScenario) -> dict:
    prompt = case.prompt
    instant = utility_reply(prompt)
    route = choose_route(prompt, "auto", 4096, 4096)
    structured = is_live_structured_question(prompt)
    web = should_use_web(prompt, "auto")
    atomic = is_atomic_knowledge_question(prompt)
    memory = requires_memory_context(prompt)
    context = requires_conversation_context(prompt)

    if instant is not None:
        path = "instant"
    elif structured:
        path = "structured"
    elif web:
        path = "web"
    elif atomic:
        path = "atomic"
    elif memory:
        path = "memory"
    elif context:
        path = "context"
    elif route.max_output_tokens >= 3000:
        path = "longform"
    else:
        path = "direct"

    return {
        "id": case.id,
        "category": case.category,
        "prompt": case.prompt,
        "expected": case.expected_path,
        "actual": path,
        "ok": path == case.expected_path,
        "instant_kind": instant.kind if instant else "",
        "structured": structured,
        "web": web,
        "atomic": atomic,
        "memory": memory,
        "context": context,
        "route_mode": route.mode,
        "max_output_tokens": route.max_output_tokens,
        "reasoning": route.reasoning,
        "route_reason": route.reason,
    }


def audit() -> dict:
    rows = [classify(case) for case in SCENARIOS]
    errors = [f"{row['id']}:{row['expected']}!={row['actual']}:{row['prompt']}" for row in rows if not row["ok"]]
    by_expected = Counter(case.expected_path for case in SCENARIOS)
    by_actual = Counter(row["actual"] for row in rows)
    category_failures: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not row["ok"]:
            category_failures[row["category"]].append(row["id"])

    # Product invariants discovered from real-user simulation.
    lookup = {row["id"]: row for row in rows}
    invariants: list[str] = []
    if lookup["U004"]["actual"] != "instant":
        invariants.append("tokyo_time_must_be_instant")
    if lookup["U021"]["actual"] != "atomic" or lookup["U021"]["web"]:
        invariants.append("stable_writer_must_skip_web")
    if lookup["U041"]["actual"] != "web":
        invariants.append("chess_advice_must_use_web")
    if lookup["U051"]["actual"] != "web":
        invariants.append("current_president_must_use_web")
    if lookup["U074"]["actual"] != "memory":
        invariants.append("memory_question_must_load_memory")
    if lookup["U091"]["actual"] != "atomic":
        invariants.append("stable_exchange_rate_definition_must_skip_web")
    if lookup["U094"]["actual"] != "web":
        invariants.append("software_version_must_use_web")
    if lookup["U095"]["actual"] != "direct":
        invariants.append("self_contained_edit_must_skip_old_history")
    if lookup["U100"]["actual"] != "web":
        invariants.append("future_release_must_use_web")

    errors.extend(invariants)
    return {
        "format": "olya-real-user-routing-audit-v1",
        "status": "passed" if not errors else "failed",
        "users": len(rows),
        "passed": sum(1 for row in rows if row["ok"]),
        "failed": sum(1 for row in rows if not row["ok"]),
        "errors": errors,
        "expected_distribution": dict(sorted(by_expected.items())),
        "actual_distribution": dict(sorted(by_actual.items())),
        "category_failures": dict(sorted(category_failures.items())),
        "failures": [row for row in rows if not row["ok"]],
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
