from __future__ import annotations

from dataclasses import dataclass

from app.services.freshness import classify_freshness


@dataclass(frozen=True)
class ResearchPlan:
    intent: str
    queries: list[str]
    freshness: str
    source_mix: list[str]
    freshness_category: str = "stable"
    freshness_reason: str = ""
    freshness_max_age_seconds: int = 0
    freshness_min_independent_hosts: int = 0


def _software_queries(clean: str) -> list[str]:
    value = clean.casefold()
    official = (
        (("python",), "latest Python release site:python.org"),
        (("node.js", "nodejs", "node "), "latest Node.js release site:nodejs.org"),
        (("php",), "latest PHP release site:php.net"),
        (("postgresql", "postgres "), "latest PostgreSQL release site:postgresql.org"),
        (("docker",), "Docker Engine release notes site:docs.docker.com"),
        (("nginx",), "latest nginx release site:nginx.org"),
        (("react",), "latest React release site:react.dev"),
        (("fastapi",), "latest FastAPI release site:fastapi.tiangolo.com"),
    )
    primary = next((query for markers, query in official if any(marker in value for marker in markers)), "")
    if primary:
        # Primary source first, then the user's natural wording for an independent
        # cross-check. Keep a third changelog query only for deeper research.
        return [primary, clean, f"{clean} changelog"]
    return [clean, f"{clean} official release", f"{clean} changelog"]


def plan_research(question: str, *, intent: str = "general", location: str | None = None, category: str | None = None) -> ResearchPlan:
    clean = " ".join(question.split()).strip()
    if not clean:
        raise ValueError("question is required")

    if intent == "local_business":
        place = " ".join((location or "").split()).strip()
        kind = " ".join((category or clean).split()).strip()
        if not place:
            raise ValueError("location is required for local business research")
        if not kind:
            raise ValueError("category is required for local business research")
        base = f"{kind} {place}"
        return ResearchPlan(
            intent="local_business",
            queries=[
                base,
                f"{base} отзывы Яндекс 2ГИС ПроДокторов",
                f"{base} цены специалисты",
                f"{base} лицензия официальный сайт",
                f"{base} рейтинг",
            ],
            freshness="current",
            source_mix=["official", "maps_catalog", "reviews", "pricing", "professional", "independent"],
            freshness_category="availability",
            freshness_reason="Локальные компании, репутация, специалисты, цены и доступность требуют актуальной проверки.",
            freshness_max_age_seconds=6 * 60 * 60,
            freshness_min_independent_hosts=2,
        )

    verdict = classify_freshness(clean)
    if not verdict.required:
        return ResearchPlan(
            intent="general",
            queries=[clean],
            freshness="stable",
            source_mix=["primary", "independent"],
            freshness_category=verdict.category,
            freshness_reason=verdict.reason,
        )

    queries = [clean, f"{clean} официальный источник", f"{clean} последние данные"]
    if verdict.category == "news":
        queries = [clean, f"{clean} сегодня", f"{clean} официальный источник"]
    elif verdict.category == "software_version":
        queries = _software_queries(clean)
    elif verdict.category == "law":
        queries = [clean, f"{clean} официальный текст", f"{clean} действует сейчас"]

    return ResearchPlan(
        intent="general",
        queries=queries,
        freshness="current",
        source_mix=["primary", "independent", "secondary"],
        freshness_category=verdict.category,
        freshness_reason=verdict.reason,
        freshness_max_age_seconds=verdict.max_age_seconds,
        freshness_min_independent_hosts=verdict.min_independent_hosts,
    )
