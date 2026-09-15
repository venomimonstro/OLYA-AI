#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json

from app.services.clean_web import build_clean_web_context, should_use_web
from app.services.discovery import SearchHit
from app.services.research import FetchedPage


class FakeDiscovery:
    async def search(self, query: str, *, count: int = 5, country=None, language=None):
        return [
            SearchHit(
                query=query,
                title=f"Источник {i}: полезные рекомендации по теме",
                url=f"https://example{i}.test/article",
                snippet=("Практическая рекомендация с конкретным объяснением и полезным примером. " * 8),
                rank=i,
                provider="fixture",
            )
            for i in range(1, 6)
        ]


class FakeFetcher:
    async def fetch(self, url: str):
        content = (
            "Полезная рекомендация. Она относится к вопросу пользователя и объясняет, что делать на практике.\n\n"
            "Вторая длинная часть страницы специально раздута, но не должна целиком попасть в prompt. " * 50
        )
        return FetchedPage(url, url, "Fixture", content, 200, "text/html", {})


async def audit_async() -> dict:
    errors: list[str] = []
    advice = await build_clean_web_context(
        discovery=FakeDiscovery(),
        fetcher=FakeFetcher(),
        question="Что нужно знать, чтобы чаще побеждать в шахматах?",
        web_mode="auto",
        deep=False,
    )
    advice_chars = sum(len(item.content) for item in advice.context_messages)
    if len(advice.sources) != 5:
        errors.append("advice_source_count")
    if advice.fetched > 1:
        errors.append("advice_fetch_count")
    if advice_chars > 950:
        errors.append(f"advice_prompt_too_large:{advice_chars}")

    fresh = await build_clean_web_context(
        discovery=FakeDiscovery(),
        fetcher=FakeFetcher(),
        question="Кто сейчас президент США?",
        web_mode="auto",
        deep=False,
    )
    fresh_chars = sum(len(item.content) for item in fresh.context_messages)
    if fresh.fetched != 0:
        errors.append("fresh_query_should_not_fetch_pages")
    if fresh_chars > 950:
        errors.append(f"fresh_prompt_too_large:{fresh_chars}")

    if should_use_web("Объясни разницу между компетенциями, навыками и требованиями вакансии.", "auto"):
        errors.append("hr_explanation_false_web")
    if not should_use_web("Найди вакансии Python-разработчика в Москве", "auto"):
        errors.append("vacancy_lookup_missed_web")

    return {
        "format": "olya-web-prompt-budget-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": {
            "advice_sources": len(advice.sources),
            "advice_fetched": advice.fetched,
            "advice_prompt_chars": advice_chars,
            "fresh_sources": len(fresh.sources),
            "fresh_fetched": fresh.fetched,
            "fresh_prompt_chars": fresh_chars,
        },
    }


def main() -> int:
    result = asyncio.run(audit_async())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
