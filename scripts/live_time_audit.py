#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json

from app.live_location_parser_patch import install_live_location_parser_patch
from app.services import live_structured_facts as live


def audit() -> dict:
    errors: list[str] = []
    install_live_location_parser_patch()

    samples = {
        "какое время в токио сейчас?": "Tokyo",
        "который час в москве?": "Москва",
        "сколько сейчас времени в лондоне?": "London",
        "what is the time in New York now?": "New York",
    }
    parsed: dict[str, str] = {}
    detected: dict[str, bool] = {}
    for question, expected in samples.items():
        location = live._location_from_question(question)
        parsed[question] = location
        detected[question] = bool(live.is_local_time_question(question))
        if location != expected:
            errors.append(f"location:{question}:{location!r}!={expected!r}")
        if not detected[question]:
            errors.append(f"intent:{question}")

    async def resolve_tokyo():
        return await live.resolve_live_structured_fact("какое время в токио сейчас?")

    result = asyncio.run(resolve_tokyo())
    answer = str(getattr(result, "resolved_answer", "") or "") if result is not None else ""
    authoritative = bool(getattr(result, "authoritative_evidence", False)) if result is not None else False
    if result is None:
        errors.append("tokyo_resolver_returned_none")
    if "Tokyo" not in answer and "Токио" not in answer:
        errors.append("tokyo_answer_missing_location")
    if "Asia/Tokyo" not in answer:
        errors.append("tokyo_answer_missing_timezone")
    if not authoritative:
        errors.append("tokyo_answer_not_authoritative")

    return {
        "format": "olya-live-time-audit-v2",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "intent_detection": detected,
        "locations": parsed,
        "tokyo_answer": answer,
        "authoritative": authoritative,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
