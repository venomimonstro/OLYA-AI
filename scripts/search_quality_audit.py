#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import app.api.routes  # noqa: F401 - installs runtime patches
from app import search_quality_patch
from app.services import discovery
from app.services import task_solver
from app.services.discovery import SearchHit
from app.services.searxng_discovery import SearxngDiscovery, _relevance_score


ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []

    official = SearchHit(
        query="current president united states",
        title="The White House",
        url="https://www.whitehouse.gov/administration/",
        snippet="Official administration information",
        rank=5,
        provider="searxng:bing,startpage",
    )
    blog = SearchHit(
        query="current president united states",
        title="Who is the US president today",
        url="https://example-blog.test/president",
        snippet="Article discussing the current president",
        rank=1,
        provider="searxng:bing",
    )
    docs = SearchHit(
        query="python documentation",
        title="Python Documentation",
        url="https://docs.python.org/3/",
        snippet="Official Python documentation reference",
        rank=4,
        provider="searxng:bing,startpage",
    )

    official_row = discovery.enrich_hit(official)
    blog_row = discovery.enrich_hit(blog)
    docs_row = discovery.enrich_hit(docs)
    if official_row.get("source_kind") != "primary_official":
        errors.append("official_not_primary")
    if docs_row.get("source_kind") not in {"primary_official", "documentation"}:
        errors.append("docs_not_authoritative")
    if float(official_row.get("discovery_score") or 0) <= float(blog_row.get("discovery_score") or 0):
        errors.append("official_score_not_higher")

    ranked = task_solver.diversify_hits([blog, official], kind="web_research", limit=2)
    if not ranked or "whitehouse.gov" not in str(ranked[0].get("url") or ""):
        errors.append("primary_source_not_first")

    relevance = {
        "whitehouse_good": _relevance_score(
            "current President of the United States site:whitehouse.gov",
            title="The White House — Administration",
            url="https://www.whitehouse.gov/administration/",
            snippet="President and administration information",
        ),
        "gemini_bad": _relevance_score(
            "current President of the United States site:whitehouse.gov",
            title="Google Gemini",
            url="https://gemini.google.com/?hl=fr",
            snippet="",
        ),
        "python_good": _relevance_score(
            "latest Python version site:python.org",
            title="Download Python",
            url="https://www.python.org/downloads/",
            snippet="Latest Python releases",
        ),
        "excel_bad": _relevance_score(
            "latest Python version site:python.org",
            title="Makro ausführen, wenn Zellinhalt sich ändert",
            url="https://www.herber.de/forum/excel",
            snippet="Excel Makro Forum",
        ),
        "bulgakov_good": _relevance_score(
            "кто написал мастер и маргарита Булгаков",
            title="Мастер и Маргарита — Михаил Булгаков",
            url="https://example.org/bulgakov-master-margarita",
            snippet="Роман Михаила Булгакова",
        ),
        "reddit_home_bad": _relevance_score(
            "кто написал мастер и маргарита Булгаков",
            title="Reddit - Dive into anything",
            url="https://www.reddit.com/",
            snippet="",
        ),
    }
    for key in ("whitehouse_good", "python_good", "bulgakov_good"):
        if relevance[key] <= 0:
            errors.append(f"relevance_false_negative:{key}")
    for key in ("gemini_bad", "excel_bad", "reddit_home_bad"):
        if relevance[key] > 0:
            errors.append(f"relevance_false_positive:{key}")

    live_ttls = {
        "usd_rub": search_quality_patch._cache_ttl("USD/RUB курс сейчас", 3600),
        "weather": search_quality_patch._cache_ttl("погода Москва сейчас", 3600),
        "bitcoin": search_quality_patch._cache_ttl("bitcoin price current", 3600),
        "software": search_quality_patch._cache_ttl("latest Python version", 3600),
        "stable": search_quality_patch._cache_ttl("кто написал мастер и маргарита", 3600),
    }
    if max(live_ttls["usd_rub"], live_ttls["weather"], live_ttls["bitcoin"]) > 90:
        errors.append("live_cache_too_long")
    if live_ttls["software"] > 900:
        errors.append("software_cache_too_long")
    if live_ttls["stable"] < 3600:
        errors.append("stable_cache_unnecessarily_short")

    engine_pool = list(SearxngDiscovery.general_engines)
    if engine_pool != ["bing", "startpage"]:
        errors.append("interactive_engine_pool_not_bounded")

    settings_text = (ROOT / "searxng" / "settings.yml").read_text("utf-8")
    if "keep_only:" not in settings_text or "- bing" not in settings_text or "- startpage" not in settings_text:
        errors.append("searx_keep_only_missing")
    for unexpected in ("wikidata", "google cse", "duckduckgo", "qwant", "yandex", "brave"):
        if f"- {unexpected}" in settings_text:
            errors.append(f"unexpected_keep_only_engine:{unexpected}")

    compose_text = (ROOT / "docker-compose.yml").read_text("utf-8")
    if "'engines':'bing'" not in compose_text and '"engines":"bing"' not in compose_text:
        errors.append("searx_healthcheck_not_single_engine")

    grounding_text = (ROOT / "app" / "services" / "fast_web_grounding.py").read_text("utf-8")
    for marker in ("max_chars: int = 650", "fetched[:2]", "discovery_rows", "evidence_chars"):
        if marker not in grounding_text:
            errors.append(f"evidence_budget_marker_missing:{marker}")

    policy_path = search_quality_patch.__file__.replace("search_quality_patch.py", "response_policy_patch.py")
    try:
        policy_text = open(policy_path, "r", encoding="utf-8").read()
    except OSError:
        policy_text = ""
    for marker in (
        "STRUCTURED OFFICIAL FACT",
        "Source authority is more important than raw search rank",
        "Do not introduce a person, number, date, quote, version or price",
    ):
        if marker not in policy_text:
            errors.append(f"policy_marker_missing:{marker[:28]}")

    return {
        "format": "olya-search-quality-audit-v4",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "engine_pool": engine_pool,
        "authority": {
            "official": official_row,
            "documentation": docs_row,
            "ordinary_web": blog_row,
            "ranked_urls": [row.get("url") for row in ranked],
        },
        "relevance": relevance,
        "cache_ttl_seconds": live_ttls,
        "evidence_budget": {"verified_sources": 2, "excerpt_chars_each": 650, "extra_search_rows": 2},
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
