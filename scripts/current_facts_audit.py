#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.current_fact_evidence_guard import resolve_current_office_holder
from app.schemas.chat import ChatMessage
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

    sample_messages = [
        ChatMessage(
            role="user",
            content=(
                "VERIFIED FRESH WEB SNAPSHOTS.\n\n"
                "[VERIFIED SOURCE 1]\n"
                "Title: The Administration\n"
                "URL: https://www.whitehouse.gov/administration/\n"
                "Fetched at: 2026-09-14T10:00:00+00:00\n"
                "Content excerpt:\nThe Administration\nPresident Donald J. Trump\n"
                "45th & 47th President of the United States\n"
            ),
        ),
        ChatMessage(role="user", content=question),
    ]
    resolved = resolve_current_office_holder(question, sample_messages)
    if not resolved or "Дональд Трамп" not in resolved:
        errors.append({"code": "authoritative_role_resolution_failed", "resolved": resolved})
    if resolved and "Байден" in resolved:
        errors.append({"code": "stale_holder_survived_evidence_guard", "resolved": resolved})

    smart = (ROOT / "app" / "api" / "routes" / "smart_chat.py").read_text("utf-8")
    fast = (ROOT / "app" / "services" / "fast_web_grounding.py").read_text("utf-8")
    policy = (ROOT / "app" / "response_policy_patch.py").read_text("utf-8")
    evidence_guard = (ROOT / "app" / "current_fact_evidence_guard.py").read_text("utf-8")
    search_policy = (ROOT / "app" / "fresh_search_policy_patch.py").read_text("utf-8")
    searx_client = (ROOT / "app" / "services" / "searxng_discovery.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")

    # Do not read /app/searxng/settings.yml here. Production app images do not
    # contain the sidecar's bind-mounted configuration by design. Engine
    # availability/provenance is verified separately by current_search_live_probe
    # against the running SearXNG service.
    required = {
        "mandatory_fresh_search": (smart, "mandatory_fresh"),
        "hold_unverified_stream": (smart, "token_sink = None if mandatory_fresh else job.token"),
        "freshness_failure_guard": (smart, "_freshness_unavailable"),
        "official_whitehouse_query": (fast, "current President of the United States site:whitehouse.gov"),
        "official_whitehouse_page": (fast, "https://www.whitehouse.gov/administration/"),
        "verified_snapshot_context": (fast, "VERIFIED FRESH WEB SNAPSHOTS"),
        "same_language_policy": (policy, "Always answer in the language of the user's latest message"),
        "evidence_over_memory": (policy, "external evidence supplied in the context is authoritative"),
        "deterministic_evidence_guard": (evidence_guard, "resolve_current_office_holder"),
        "office_holder_no_cache": (search_policy, "ttl_seconds = 0"),
        "five_minute_other_fresh_cache": (search_policy, "300"),
        "fresh_quality_mode": (search_policy, "quality_mode = True"),
        "evidence_guard_installed": (bootstrap, "install_current_fact_evidence_guard()"),
        "fresh_search_policy_installed": (bootstrap, "install_fresh_search_policy_patch()"),
        "explicit_engine_query": (searx_client, '"engines": ",".join(self.general_engines)'),
        "engine_provenance": (searx_client, "_provider_name"),
        "google_engine_client": (searx_client, '"google"'),
        "yandex_engine_client": (searx_client, '"yandex"'),
        "duckduckgo_engine_client": (searx_client, '"duckduckgo"'),
    }
    for code, (text, marker) in required.items():
        if marker not in text:
            errors.append({"code": code, "missing": marker})

    return {
        "format": "olya-current-facts-audit-v3",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "president_freshness": {
            "required": decision.required,
            "category": decision.category,
            "min_independent_hosts": decision.min_independent_hosts,
        },
        "deterministic_sample": resolved,
        "office_holder_cache_seconds": 0,
        "other_fresh_cache_seconds": 300,
        "search_engine_client_policy": ["google", "yandex", "bing", "duckduckgo", "brave", "startpage", "qwant"],
        "live_engine_verification": "run scripts.current_search_live_probe",
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
