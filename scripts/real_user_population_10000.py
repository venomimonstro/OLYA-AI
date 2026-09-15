#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import re

from scripts.real_user_scenarios import SCENARIOS, UserScenario


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    segment: str


@dataclass(frozen=True)
class SyntheticSession:
    id: str
    base_id: str
    persona: str
    persona_label: str
    segment: str
    style: str
    prompt: str
    expected_path: str
    category: str
    min_chars: int
    max_chars: int
    must_include: tuple[str, ...]
    expect_web: bool | None
    max_ttft_ms: int
    max_total_ms: int


PERSONAS: tuple[Persona, ...] = (
    Persona("everyday", "Обычный пользователь", "consumer"),
    Persona("homemaker", "Домохозяйка / родитель", "consumer"),
    Persona("teen", "Подросток", "youth"),
    Persona("school", "Школьник", "youth"),
    Persona("student_humanities", "Студент-гуманитарий", "student"),
    Persona("student_stem", "Студент технического направления", "student"),
    Persona("senior", "Пожилой пользователь", "consumer"),
    Persona("office", "Офисный сотрудник", "work"),
    Persona("entrepreneur", "Предприниматель", "business"),
    Persona("marketer", "Маркетолог", "business"),
    Persona("smm", "SMM-специалист", "business"),
    Persona("seo", "SEO-специалист", "business"),
    Persona("developer", "Программист", "technical"),
    Persona("sysadmin", "Системный администратор", "technical"),
    Persona("designer", "Дизайнер", "creative"),
    Persona("freelancer", "Фрилансер", "work"),
    Persona("seller", "Продавец на маркетплейсе", "business"),
    Persona("hr", "HR / рекрутер", "work"),
    Persona("accountant", "Бухгалтер", "work"),
    Persona("traveler", "Путешественник", "consumer"),
)

STYLES = ("exact", "polite", "mobile", "colloquial", "noisy")


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _strip_terminal(text: str) -> str:
    return text.rstrip().rstrip("?!.")


def _style_prompt(prompt: str, style: str, *, base: UserScenario) -> str:
    text = str(prompt or "").strip()
    if style == "exact":
        return text
    if style == "polite":
        # Greetings do not naturally become "подскажи привет".
        if base.category == "instant" and re.match(r"^(?:привет|здравств|добро)", text, re.I):
            return text
        return "Подскажи, пожалуйста, " + _lower_first(text)
    if style == "mobile":
        # Typical phone input: lower case, little punctuation, compact spacing.
        return " ".join(_strip_terminal(text).lower().split())
    if style == "colloquial":
        value = text.replace("сейчас", "щас").replace("Сейчас", "Щас")
        value = value.replace("пожалуйста", "плиз")
        return "слушай, " + _lower_first(_strip_terminal(value))
    if style == "noisy":
        # Harmless noise/format variation without changing semantic keywords.
        value = _strip_terminal(text)
        value = re.sub(r"\s+", "  ", value, count=1)
        return value + " 🙏"
    raise ValueError(style)


def build_population() -> tuple[SyntheticSession, ...]:
    rows: list[SyntheticSession] = []
    serial = 0
    for base in SCENARIOS:
        for persona in PERSONAS:
            for style in STYLES:
                serial += 1
                rows.append(SyntheticSession(
                    id=f"S{serial:05d}",
                    base_id=base.id,
                    persona=persona.id,
                    persona_label=persona.label,
                    segment=persona.segment,
                    style=style,
                    prompt=_style_prompt(base.prompt, style, base=base),
                    expected_path=base.expected_path,
                    category=base.category,
                    min_chars=base.min_chars,
                    max_chars=base.max_chars,
                    must_include=base.must_include,
                    expect_web=base.expect_web,
                    max_ttft_ms=base.max_ttft_ms,
                    max_total_ms=base.max_total_ms,
                ))
    result = tuple(rows)
    assert len(result) == 10_000
    assert len({row.id for row in result}) == 10_000
    return result


POPULATION = build_population()


if __name__ == "__main__":
    from collections import Counter
    import json
    print(json.dumps({
        "format": "olya-real-user-population-v1",
        "sessions": len(POPULATION),
        "personas": len(PERSONAS),
        "styles": list(STYLES),
        "categories": dict(Counter(row.category for row in POPULATION)),
        "segments": dict(Counter(row.segment for row in POPULATION)),
    }, ensure_ascii=False, indent=2))
