#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.public_brand_policy import sanitize_public_branding
from app.services.fast_web_grounding import should_auto_ground
from app.services.freshness import classify_freshness

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[dict] = []
    question = "кто сейчас президент сша?"
    decision = classify_freshness(question)
    if not decision.required or decision.category != "official_role":
        errors.append({"code": "president_not_current_role", "decision": decision.__dict__})
    if not should_auto_ground(question):
        errors.append({"code": "president_not_auto_grounded"})

    sample = "Локальная Qwen, файлы проекта и поиск"
    sanitized = sanitize_public_branding(sample)
    if "qwen" in sanitized.casefold():
        errors.append({"code": "vendor_name_leaked", "sanitized": sanitized})
    if "файлы проекта" not in sanitized:
        errors.append({"code": "branding_sanitizer_damaged_neighbor_text", "sanitized": sanitized})

    smart = (ROOT / "app" / "api" / "routes" / "smart_chat.py").read_text("utf-8")
    fast = (ROOT / "app" / "services" / "fast_web_grounding.py").read_text("utf-8")
    policy = (ROOT / "app" / "response_policy_patch.py").read_text("utf-8")

    required = {
        "mandatory_fresh_search": (smart, "mandatory_fresh"),
        "hold_unverified_stream": (smart, "token_sink = None if mandatory_fresh else job.token"),
        "freshness_failure_guard": (smart, "_freshness_unavailable"),
        "russian_boilerplate_guard": (smart, "please note that"),
        "official_whitehouse_query": (fast, "current President of the United States site:whitehouse.gov"),
        "official_whitehouse_page": (fast, "https://www.whitehouse.gov/administration/"),
        "verified_snapshot_context": (fast, "VERIFIED FRESH WEB SNAPSHOTS"),
        "same_language_policy": (policy, "Always answer in the language of the user's latest message"),
        "evidence_over_memory": (policy, "external evidence supplied in the context is authoritative"),
    }
    for code, (text, marker) in required.items():
        if marker not in text:
            errors.append({"code": code, "missing": marker})

    return {
        "format": "olya-current-facts-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "president_freshness": {
            "required": decision.required,
            "category": decision.category,
            "min_independent_hosts": decision.min_independent_hosts,
        },
        "public_branding_sample": sanitized,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
