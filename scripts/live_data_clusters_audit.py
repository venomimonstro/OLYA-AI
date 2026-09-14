#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

# Install exactly the same early runtime patches that production routes use.
import app.api.routes  # noqa: F401
from app.services import structured_facts
from app.services.freshness import classify_freshness
from app.services.live_structured_facts import (
    _location_from_question,
    is_crypto_price_question,
    is_key_rate_question,
    is_live_structured_question,
    is_local_time_question,
    is_weather_question,
)

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    cases = [
        ("какой курс доллар рубль сейчас?", "market", "fiat"),
        ("сколько сейчас стоит биткоин?", "market", "crypto"),
        ("какая сейчас ключевая ставка Банка России?", "market", "key_rate"),
        ("какая погода сейчас в Москве?", "weather", "weather"),
        ("сколько времени сейчас в Токио?", "recent_general", "local_time"),
    ]
    matrix: list[dict] = []
    for question, expected_freshness, kind in cases:
        freshness = classify_freshness(question)
        patched = bool(structured_facts.is_currency_rate_question(question))
        live = is_live_structured_question(question)
        if not patched:
            errors.append(f"not_routed_to_structured_lane:{kind}")
        if not live:
            errors.append(f"not_detected_by_live_router:{kind}")
        if freshness.category != expected_freshness:
            errors.append(f"freshness:{kind}:{freshness.category}!={expected_freshness}")
        matrix.append({
            "kind": kind,
            "question": question,
            "freshness": freshness.category,
            "structured_lane": patched,
        })

    if not is_crypto_price_question("курс BTC сейчас"):
        errors.append("crypto_detector_btc")
    if not is_crypto_price_question("сколько стоит ethereum сегодня"):
        errors.append("crypto_detector_eth")
    if not is_key_rate_question("ставка ЦБ сейчас"):
        errors.append("key_rate_detector")
    if not is_weather_question("прогноз погоды завтра в Казани"):
        errors.append("weather_detector")
    if not is_local_time_question("который час в Лондоне"):
        errors.append("time_detector")

    locations = {
        "какая погода сегодня в Москве?": "Москва",
        "погода завтра в Санкт-Петербурге": "Санкт-Петербург",
        "сколько времени сейчас в Токио?": "Tokyo",
        "погода в Екатеринбурге": "Екатеринбург",
        "погода в Казани": "Казань",
    }
    normalized: dict[str, str] = {}
    for question, expected in locations.items():
        actual = _location_from_question(question)
        normalized[question] = actual
        if actual != expected:
            errors.append(f"location:{question}:{actual}!={expected}")

    source = (ROOT / "app" / "services" / "live_structured_facts.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")
    patch = (ROOT / "app" / "live_structured_fact_patch.py").read_text("utf-8")
    required_markers = {
        "crypto_two_provider_gate": "len(observations) < 2",
        "crypto_divergence_guard": "> 0.06",
        "coingecko": "api.coingecko.com",
        "cryptocompare": "min-api.cryptocompare.com",
        "binance": "api.binance.com",
        "coinbase": "api.coinbase.com",
        "open_meteo_forecast": "api.open-meteo.com/v1/forecast",
        "open_meteo_geocoding": "geocoding-api.open-meteo.com/v1/search",
        "cbr_key_rate": "www.cbr.ru/hd_base/KeyRate/",
        "parallel_crypto_fetch": "asyncio.gather",
    }
    for name, marker in required_markers.items():
        if marker not in source:
            errors.append(f"missing:{name}")
    if "install_live_structured_fact_patch()" not in bootstrap:
        errors.append("bootstrap_live_structured_patch_missing")
    if "return await current_resolver(question)" not in patch:
        errors.append("fiat_duplicate_fallback_guard_missing")

    return {
        "format": "olya-live-data-clusters-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "matrix": matrix,
        "normalized_locations": normalized,
        "clusters": ["fiat_fx", "crypto", "key_rate", "weather", "local_time"],
        "generic_web_fallback": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
