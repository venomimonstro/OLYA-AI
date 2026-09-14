from __future__ import annotations

import re
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo


def install_live_location_parser_patch() -> None:
    """Robust natural-language location/time parsing for fast live facts."""
    from app.services import live_structured_facts as live

    current_location = live._location_from_question
    current_time_detector = live.is_local_time_question
    current_time_resolver = live._resolve_local_time
    if getattr(current_location, "_olya_location_v3", False):
        return

    trailing_recency = re.compile(
        r"(?:\s+(?:сейчас|сегодня|завтра|now|today|tomorrow|right\s+now))+$",
        re.IGNORECASE,
    )
    time_intent = re.compile(
        r"(?:"
        r"\bкоторый\s+час\b|"
        r"\bсколько\s+(?:сейчас\s+)?времени\b|"
        r"\bкакое\s+(?:сейчас\s+)?время\b|"
        r"\bвремя\s+(?:сейчас\s+)?(?:в|во)\b|"
        r"\bсейчас\s+время\s+(?:в|во)\b|"
        r"\bwhat(?:'s|\s+is)?\s+the\s+time\b|"
        r"\bcurrent\s+time\b|"
        r"\btime\s+(?:right\s+now\s+)?in\b"
        r")",
        re.IGNORECASE,
    )

    direct_timezones = {
        "Москва": "Europe/Moscow",
        "Санкт-Петербург": "Europe/Moscow",
        "Казань": "Europe/Moscow",
        "Самара": "Europe/Samara",
        "Екатеринбург": "Asia/Yekaterinburg",
        "Новосибирск": "Asia/Novosibirsk",
        "Красноярск": "Asia/Krasnoyarsk",
        "Тюмень": "Asia/Yekaterinburg",
        "Уфа": "Asia/Yekaterinburg",
        "Пермь": "Asia/Yekaterinburg",
        "Сочи": "Europe/Moscow",
        "Челябинск": "Asia/Yekaterinburg",
        "Ижевск": "Europe/Samara",
        "Барнаул": "Asia/Barnaul",
        "Tokyo": "Asia/Tokyo",
        "London": "Europe/London",
        "Dubai": "Asia/Dubai",
        "New York": "America/New_York",
        "Berlin": "Europe/Berlin",
        "Paris": "Europe/Paris",
        "Amsterdam": "Europe/Amsterdam",
    }

    def parse_location(question: str) -> str:
        text = " ".join(str(question or "").strip().rstrip("?.!").split())
        if not text:
            return ""
        normalized = trailing_recency.sub("", text).strip()
        patterns = (
            re.compile(r"\b(?:в|во)\s+([А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z .'-]{0,70})$", re.IGNORECASE),
            re.compile(r"\b(?:in|at)\s+([A-Za-z][A-Za-z .'-]{0,70})$", re.IGNORECASE),
            re.compile(r"\b(?:для)\s+([А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z .'-]{0,70})$", re.IGNORECASE),
        )
        for pattern in patterns:
            match = pattern.search(normalized)
            if not match:
                continue
            value = match.group(1).strip(" ,")
            if value:
                return live._canonical_location(value)
        match = re.search(
            r"\b(?:погода|weather)\s+([А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z .'-]{0,60})$",
            normalized,
            re.IGNORECASE,
        )
        if match:
            return live._canonical_location(match.group(1))
        return current_location(question)

    def detect_time(question: str) -> bool:
        text = " ".join(str(question or "").split())
        return bool(time_intent.search(text) or current_time_detector(question))

    async def resolve_time(question: str):
        location = parse_location(question)
        timezone = direct_timezones.get(location)
        if timezone:
            started = perf_counter()
            now = datetime.now(ZoneInfo(timezone))
            russian = live._russian(question)
            label = location
            if russian:
                answer = f"Сейчас в {label} {now:%H:%M}, {now:%d.%m.%Y}. Часовой пояс: {timezone}."
            else:
                answer = f"The current time in {label} is {now:%H:%M} on {now:%Y-%m-%d}. Time zone: {timezone}."
            source = {
                "title": "IANA Time Zone Database",
                "url": "https://www.iana.org/time-zones",
                "domain": "iana.org",
                "provider": "local_timezone_db",
                "source_kind": "structured_official",
                "snippet": f"location={label}; timezone={timezone}; local_time={now.isoformat()}",
                "verified": True,
                "search_confirmed": True,
                "structured": True,
            }
            return live._build_execution(
                answer=answer,
                category="schedule",
                reason="Local time was calculated from the server clock using the IANA time zone database.",
                sources=[source],
                context_text=f"Location={label}; timezone={timezone}; local_time={now.isoformat()}",
                steps=("Определяю часовой пояс", "Считаю локальное время", "Возвращаю точное время"),
                latency_ms=int((perf_counter() - started) * 1000),
                authoritative=True,
            )
        return await current_time_resolver(question)

    parse_location._olya_location_v3 = True  # type: ignore[attr-defined]
    detect_time._olya_time_intent_v2 = True  # type: ignore[attr-defined]
    resolve_time._olya_time_resolver_v2 = True  # type: ignore[attr-defined]
    live._location_from_question = parse_location
    live.is_local_time_question = detect_time
    live._resolve_local_time = resolve_time
