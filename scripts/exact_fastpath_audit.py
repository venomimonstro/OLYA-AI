#!/usr/bin/env python3
from __future__ import annotations

from decimal import Decimal
import json

from app.services.structured_facts import CurrencyValue, _answer, is_currency_rate_question
from app.utility_chat import utility_reply


def audit() -> dict:
    errors: list[str] = []
    checks: dict[str, object] = {}

    calc = utility_reply("Подскажи, пожалуйста, сколько будет 17 * 23? 🙏")
    checks["calculator"] = None if calc is None else {"kind": calc.kind, "text": calc.text}
    if calc is None or calc.kind != "calculator" or calc.text != "391":
        errors.append("calculator_regression")

    conversion = utility_reply("5 км в м 🙏")
    checks["unit_conversion"] = None if conversion is None else {"kind": conversion.kind, "text": conversion.text}
    if conversion is None or conversion.kind != "unit_conversion" or "5000" not in conversion.text:
        errors.append("unit_conversion_regression")

    nearby = utility_reply("Куда сходить сегодня?")
    checks["location_clarification"] = None if nearby is None else {"kind": nearby.kind, "text": nearby.text}
    if nearby is None or nearby.kind != "location_clarification":
        errors.append("location_clarification_regression")

    questions = (
        "100 долларов в рублях",
        "10 000 рублей в долларах",
        "25000 рублей в юанях",
        "курс доллара к рублю",
    )
    checks["fiat_intents"] = {question: is_currency_rate_question(question) for question in questions}
    if not all(checks["fiat_intents"].values()):
        errors.append("fiat_conversion_intent_regression")

    rate = [CurrencyValue(code="USD", name="доллар США", unit_rate=Decimal("80"))]
    to_rub = _answer("100 долларов в рублях", "15.09.2026", rate)
    to_usd = _answer("10 000 рублей в долларах", "15.09.2026", rate)
    checks["fiat_math"] = {"to_rub": to_rub, "to_usd": to_usd}
    if "100 USD = 8000 ₽" not in to_rub or "1 USD = 80,0000 ₽" not in to_rub:
        errors.append("foreign_to_rub_math_regression")
    if "10000 ₽ = 125 USD" not in to_usd or "1 USD = 80,0000 ₽" not in to_usd:
        errors.append("rub_to_foreign_math_regression")

    return {
        "format": "olya-exact-fastpath-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
