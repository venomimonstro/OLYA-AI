from __future__ import annotations


def install_live_structured_fact_patch() -> None:
    """Expand the existing structured-fact hook beyond fiat currency.

    smart_chat imports ``is_currency_rate_question``/``resolve_structured_fact``
    from app.services.structured_facts. Installing this patch before route modules
    bind those names lets the same proven fast lane cover fiat FX, the Bank of
    Russia key rate, crypto quotes, weather and local time without duplicating
    the chat orchestrator.
    """
    from app.services import structured_facts as legacy
    from app.services import live_structured_facts as live

    current_detector = legacy.is_currency_rate_question
    current_resolver = legacy.resolve_structured_fact
    if getattr(current_detector, "_olya_live_structured", False):
        return

    def is_live_candidate(question: str) -> bool:
        return bool(
            current_detector(question)
            or live.is_key_rate_question(question)
            or live.is_crypto_price_question(question)
            or live.is_weather_question(question)
            or live.is_local_time_question(question)
        )

    async def resolve_any(question: str):
        # Fiat FX already has its own dual-CBR resolver. If it fails, return None
        # immediately so smart_chat uses generic web fallback instead of calling
        # the same CBR endpoints a second time.
        if current_detector(question):
            return await current_resolver(question)
        return await live.resolve_live_structured_fact(question)

    is_live_candidate._olya_live_structured = True  # type: ignore[attr-defined]
    resolve_any._olya_live_structured = True  # type: ignore[attr-defined]
    legacy.is_currency_rate_question = is_live_candidate
    legacy.resolve_structured_fact = resolve_any
