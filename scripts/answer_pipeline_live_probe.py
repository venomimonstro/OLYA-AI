#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from urllib.parse import urlsplit

from app.schemas.chat import ChatMessage
from app.services.searxng_discovery import SearxngDiscovery
from app.services.structured_facts import resolve_structured_fact
from app.stable_fact_evidence_guard import _consensus as stable_fact_consensus


async def _search_probe(
    client: SearxngDiscovery,
    query: str,
    *,
    language: str,
    expected_terms: tuple[str, ...] = (),
    expected_domain: str = "",
    stable_consensus: bool = False,
) -> dict:
    started = perf_counter()
    errors: list[str] = []
    hits = []
    try:
        hits = await client.search(query, count=7, country="RU", language=language)
    except Exception as exc:
        errors.append(f"search_failed:{exc.__class__.__name__}")
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))

    observed: set[str] = set()
    domains: set[str] = set()
    text_parts: list[str] = []
    examples: list[dict] = []
    for hit in hits:
        provider = str(hit.provider or "")
        if provider.startswith("searxng:"):
            observed.update(item for item in provider.split(":", 1)[1].split(",") if item)
        host = (urlsplit(hit.url).hostname or "").casefold().removeprefix("www.")
        if host:
            domains.add(host)
        text_parts.extend((str(hit.title or ""), str(hit.snippet or "")))
        if len(examples) < 4:
            examples.append({"title": hit.title[:140], "url": hit.url, "provider": provider})

    haystack = " ".join(text_parts).casefold()
    expected_visible = not expected_terms or all(term.casefold() in haystack for term in expected_terms)
    domain_visible = not expected_domain or any(
        domain == expected_domain or domain.endswith("." + expected_domain)
        for domain in domains
    )
    if not hits:
        errors.append("no_results")
    if len(observed) < 2:
        errors.append("fewer_than_two_engines")
    if len(domains) < 2:
        errors.append("fewer_than_two_domains")
    if elapsed_ms > 3800:
        errors.append("search_too_slow")
    if not expected_visible:
        errors.append("expected_fact_not_visible_in_serp")
    if not domain_visible:
        errors.append(f"expected_domain_missing:{expected_domain}")

    consensus_value = ""
    if stable_consensus and hits:
        blocks = ["WEB SEARCH DISCOVERY. Server-fetched search evidence."]
        for index, hit in enumerate(hits[:7], start=1):
            blocks.append(
                f"[SEARCH {index}] provider={hit.provider}\n"
                f"Title: {hit.title}\nURL: {hit.url}\nSnippet: {hit.snippet}"
            )
        resolved = stable_fact_consensus(query, [ChatMessage(role="user", content="\n\n".join(blocks))])
        if resolved is None:
            errors.append("stable_fact_consensus_not_extracted")
        else:
            consensus_value = str(resolved[1])
            if expected_terms and not any(term.casefold() in consensus_value.casefold() for term in expected_terms):
                errors.append("stable_fact_consensus_wrong_entity")

    return {
        "query": query,
        "elapsed_ms": elapsed_ms,
        "result_count": len(hits),
        "observed_engines": sorted(observed),
        "independent_domains": len(domains),
        "expected_fact_visible": expected_visible,
        "expected_domain_visible": domain_visible,
        "consensus_value": consensus_value,
        "errors": errors,
        "examples": examples,
    }


async def probe() -> dict:
    errors: list[str] = []

    currency_started = perf_counter()
    currency = await resolve_structured_fact("какой курс доллар рубль сейчас?")
    currency_ms = max(0, int((perf_counter() - currency_started) * 1000))
    currency_ok = bool(
        currency is not None
        and getattr(currency, "resolved_answer", "")
        and getattr(currency, "authoritative_evidence", False)
        and any(str(row.get("domain") or "") == "cbr.ru" for row in currency.public_sources)
    )
    if not currency_ok:
        errors.append("official_currency_resolver_failed")
    if currency_ms > 3500:
        errors.append("official_currency_resolver_too_slow")

    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080").rstrip("/")
    search = SearxngDiscovery(base, timeout_seconds=5.0)
    author = await _search_probe(
        search,
        "кто написал Мастер и Маргарита",
        language="ru",
        expected_terms=("булгаков",),
        stable_consensus=True,
    )
    if author["errors"]:
        errors.extend(f"author:{item}" for item in author["errors"])

    current_role = await _search_probe(
        search,
        "current President of the United States site:whitehouse.gov",
        language="en",
        expected_domain="whitehouse.gov",
    )
    if current_role["errors"]:
        errors.extend(f"current_role:{item}" for item in current_role["errors"])

    return {
        "format": "olya-answer-pipeline-live-v2",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "currency": {
            "elapsed_ms": currency_ms,
            "authoritative": bool(getattr(currency, "authoritative_evidence", False)) if currency else False,
            "answer": str(getattr(currency, "resolved_answer", "") or "")[:500] if currency else "",
            "source": currency.public_sources[0] if currency and currency.public_sources else None,
        },
        "stable_author_search": author,
        "current_role_search": current_role,
    }


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
