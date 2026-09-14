#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.inference.router import choose_route
from app.quality_levels_ui import enhance_quality_levels
from app.reasoning_privacy_patch import strip_private_reasoning

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    simple = choose_route("Объясни простыми словами что такое CDN", "fast", 4096, 4096)
    medium = choose_route("Сравни два подхода и дай рекомендации", "work", 4096, 4096)
    high = choose_route("Проведи архитектурный аудит сложной системы и предложи план реализации", "deep", 4096, 4096)
    auto_simple = choose_route("Что такое CDN?", "auto", 4096, 4096)
    auto_complex = choose_route("Проведи архитектурный аудит и найди уязвимости", "auto", 4096, 4096)

    if simple.mode != "fast" or simple.reasoning:
        errors.append({"code": "simple_profile_invalid", "mode": simple.mode, "reasoning": simple.reasoning})
    if medium.mode != "work":
        errors.append({"code": "medium_profile_invalid", "mode": medium.mode})
    if high.mode != "deep" or not high.reasoning:
        errors.append({"code": "high_profile_invalid", "mode": high.mode, "reasoning": high.reasoning})
    if not (simple.max_output_tokens < medium.max_output_tokens < high.max_output_tokens):
        errors.append({
            "code": "output_budget_not_progressive",
            "simple": simple.max_output_tokens,
            "medium": medium.max_output_tokens,
            "high": high.max_output_tokens,
        })
    if auto_simple.mode != "fast":
        errors.append({"code": "auto_simple_not_fast", "mode": auto_simple.mode})
    if auto_complex.mode != "deep":
        errors.append({"code": "auto_complex_not_high", "mode": auto_complex.mode})

    leaked = strip_private_reasoning("<think>секретное рассуждение</think>Финальный ответ")
    if leaked != "Финальный ответ":
        errors.append({"code": "reasoning_filter_failed", "result": leaked})

    sample = '<html><head></head><body><select id="mode"><option value="auto">Авто</option><option value="fast">Fast</option><option value="work">Work</option><option value="deep">Deep</option></select></body></html>'
    enhanced = enhance_quality_levels(sample)
    for label in ("Простой", "Средний", "Высокий"):
        if label not in enhanced:
            errors.append({"code": "ui_level_missing", "label": label})
    if "olya_quality_level_v1" not in enhanced:
        errors.append({"code": "quality_choice_not_persistent"})

    policy = (ROOT / "app" / "response_policy_patch.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")
    task_ui = (ROOT / "app" / "task_solver_user_ui.py").read_text("utf-8")
    floor = (ROOT / "app" / "work_quality_floor_patch.py").read_text("utf-8")
    current_fact = (ROOT / "app" / "current_fact_latency_patch.py").read_text("utf-8")

    required = {
        "private_reasoning_policy": (policy, "never expose chain-of-thought"),
        "substantive_answer_policy": (policy, "complete and substantive response"),
        "privacy_patch_installed": (bootstrap, "install_reasoning_privacy_patch()"),
        "quality_ui_installed": (task_ui, "enhance_quality_levels(document)"),
        "legacy_floor_retired": (floor, "intentionally no longer rewrites routing decisions"),
        "fresh_lookup_fast": (current_fact, 'mode="fast"'),
    }
    for code, (text, marker) in required.items():
        if marker not in text:
            errors.append({"code": code, "missing": marker})

    return {
        "format": "olya-quality-levels-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "profiles": {
            "simple": {"mode": simple.mode, "reasoning": simple.reasoning, "max_output_tokens": simple.max_output_tokens},
            "medium": {"mode": medium.mode, "reasoning": medium.reasoning, "max_output_tokens": medium.max_output_tokens},
            "high": {"mode": high.mode, "reasoning": high.reasoning, "max_output_tokens": high.max_output_tokens},
        },
        "auto": {"simple": auto_simple.mode, "complex": auto_complex.mode},
        "reasoning_visibility": "internal_only",
        "ui_levels": ["Простой", "Средний", "Высокий"],
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
