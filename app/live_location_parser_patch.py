from __future__ import annotations

import re


def install_live_location_parser_patch() -> None:
    """Make live weather/time location extraction robust to natural phrasing.

    Examples that must resolve:
      - какое время в токио сейчас?
      - погода в москве сегодня
      - сколько времени сейчас в лондоне
      - weather in new york now
    """
    from app.services import live_structured_facts as live

    current = live._location_from_question
    if getattr(current, "_olya_location_v2", False):
        return

    trailing_recency = re.compile(
        r"(?:\s+(?:сейчас|сегодня|завтра|now|today|tomorrow|right\s+now))+$",
        re.IGNORECASE,
    )

    def parse_location(question: str) -> str:
        text = " ".join(str(question or "").strip().rstrip("?.!").split())
        if not text:
            return ""

        # Remove only trailing temporal qualifiers. This keeps the city phrase
        # intact while allowing normal user casing such as "токио".
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

        # Compact forms: "погода москва", "weather tokyo".
        match = re.search(
            r"\b(?:погода|weather)\s+([А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z .'-]{0,60})$",
            normalized,
            re.IGNORECASE,
        )
        if match:
            return live._canonical_location(match.group(1))

        # Preserve any future parser improvements by falling back to the original.
        return current(question)

    parse_location._olya_location_v2 = True  # type: ignore[attr-defined]
    live._location_from_question = parse_location
