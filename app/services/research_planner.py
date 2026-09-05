from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchPlan:
    intent: str
    queries: list[str]
    freshness: str
    source_mix: list[str]


def plan_research(question: str, *, intent: str = "general", location: str | None = None, category: str | None = None) -> ResearchPlan:
    clean = " ".join(question.split()).strip()
    if intent == "local_business":
        place = " ".join((location or "").split()).strip()
        kind = " ".join((category or clean).split()).strip()
        if not place:
            raise ValueError("location is required for local business research")
        if not kind:
            raise ValueError("category is required for local business research")
        base = f"{kind} {place}"
        queries = [
            base,
            f"{base} отзывы",
            f"{base} цены официальный сайт",
            f"{base} рейтинг",
            f"{base} специалисты услуги",
        ]
        return ResearchPlan(
            intent="local_business",
            queries=queries,
            freshness="current",
            source_mix=["official", "maps_catalog", "reviews", "independent"],
        )
    return ResearchPlan(
        intent="general",
        queries=[clean, f"{clean} официальный источник", f"{clean} последние данные"],
        freshness="current",
        source_mix=["primary", "independent", "secondary"],
    )
