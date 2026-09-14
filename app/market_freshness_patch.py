from __future__ import annotations

import re


_MARKET_PAIR = re.compile(
    r"(?:"
    r"\bкурс\s+(?:доллар(?:а|у|ом|ы)?|евро|юан(?:я|ь)?|рубл(?:я|ь)?|usd|eur|cny|rub)"
    r"(?:\s+(?:к|в|за))?\s+(?:рубл(?:ю|я|ь)?|доллар(?:у|а)?|евро|юан(?:ю|я|ь)?|usd|eur|cny|rub)?\b|"
    r"\b(?:usd|eur|cny|rub|btc|eth)\s*[/_-]?\s*(?:usd|eur|cny|rub)\b|"
    r"\b(?:доллар|доллара|евро|юань|юаня)\s+(?:рубль|рубля|рублю)\b|"
    r"\b(?:биткоин|bitcoin|btc|ethereum|эфириум|eth)\b"
    r")",
    re.IGNORECASE,
)


def install_market_freshness_patch() -> None:
    """Classify natural FX/crypto wording as market before generic recency.

    Russian users often write "курс доллар рубль" instead of the grammatical
    "курс доллара к рублю". The base classifier previously missed that form and
    downgraded it to recent_general, which in turn bypassed the atomic fast lane.
    """
    from app.services import freshness as freshness

    current = freshness.classify_freshness
    if getattr(current, "_olya_market_variants", False):
        return

    def classify_freshness(text: str):
        value = " ".join(str(text or "").split())
        if value and _MARKET_PAIR.search(value):
            return freshness.FreshnessDecision(
                required=True,
                category="market",
                reason="Курс валюты или криптовалюты является изменяемым рыночным показателем.",
                max_age_seconds=15 * 60,
                min_independent_hosts=2,
                confidence=0.98,
            )
        return current(text)

    classify_freshness._olya_market_variants = True  # type: ignore[attr-defined]
    freshness.classify_freshness = classify_freshness
