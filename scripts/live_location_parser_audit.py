#!/usr/bin/env python3
from __future__ import annotations

import json

from app.live_location_parser_patch import install_live_location_parser_patch
from app.services import live_structured_facts as live


def audit() -> dict:
    install_live_location_parser_patch()
    cases = {
        "какое время в токио сейчас?": "Tokyo",
        "сколько времени сейчас в лондоне": "London",
        "погода в москве сегодня": "Москва",
        "погода в санкт-петербурге завтра": "Санкт-Петербург",
        "weather in new york now": "new york",
    }
    errors: list[str] = []
    resolved: dict[str, str] = {}
    for question, expected in cases.items():
        value = live._location_from_question(question)
        resolved[question] = value
        if value.casefold() != expected.casefold():
            errors.append(f"{question!r}: expected {expected!r}, got {value!r}")

    tokyo = "какое время в токио сейчас?"
    if not live.is_local_time_question(tokyo):
        errors.append("tokyo_query_not_classified_as_local_time")
    if not live.is_live_structured_question(tokyo):
        errors.append("tokyo_query_not_classified_as_live_structured")

    return {
        "format": "olya-live-location-parser-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "resolved": resolved,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
