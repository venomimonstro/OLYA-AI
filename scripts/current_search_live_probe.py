#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx


ENGINES = ("google", "yandex", "bing", "duckduckgo", "brave", "startpage", "qwant")
QUERY = "current President of the United States"


def _engines_from_row(row: dict) -> list[str]:
    values: list[str] = []
    raw = row.get("engines")
    if isinstance(raw, list):
        values.extend(str(item).strip().lower() for item in raw if str(item).strip())
    engine = str(row.get("engine") or "").strip().lower()
    if engine:
        values.append(engine)
    return list(dict.fromkeys(values))


def main() -> int:
    base = os.getenv("X1_SEARXNG_BASE_URL", "http://searxng:8080").rstrip("/")
    params = {
        "q": QUERY,
        "format": "json",
        "language": "en",
        "safesearch": 1,
        "categories": "general",
        "engines": ",".join(ENGINES),
    }
    errors: list[str] = []
    rows: list[dict] = []
    try:
        with httpx.Client(timeout=12.0, trust_env=False) as client:
            response = client.get(f"{base}/search", params=params, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
    except Exception as exc:
        errors.append(f"search_request_failed:{exc.__class__.__name__}")

    observed: set[str] = set()
    examples: list[dict] = []
    official_hit = False
    current_holder_signal = False
    for row in rows:
        observed.update(_engines_from_row(row))
        title = str(row.get("title") or "")
        content = str(row.get("content") or row.get("snippet") or "")
        url = str(row.get("url") or "")
        host = (urlsplit(url).hostname or "").lower()
        text = f"{title} {content}".casefold()
        if host.endswith("whitehouse.gov"):
            official_hit = True
        if "donald" in text and "trump" in text:
            current_holder_signal = True
        if len(examples) < 6:
            examples.append(
                {
                    "title": title[:120],
                    "url": url,
                    "engines": _engines_from_row(row),
                }
            )

    major_seen = sorted(set(ENGINES) & observed)
    if not rows:
        errors.append("no_search_results")
    if len(major_seen) < 2:
        errors.append("fewer_than_two_search_engines_observed")
    if not official_hit:
        errors.append("whitehouse_not_present_in_metasearch_results")
    if not current_holder_signal:
        errors.append("current_holder_not_visible_in_search_results")

    result = {
        "format": "olya-current-search-live-v1",
        "status": "passed" if not errors else "degraded",
        "errors": errors,
        "query": QUERY,
        "requested_engines": list(ENGINES),
        "observed_engines": major_seen,
        "result_count": len(rows),
        "official_whitehouse_result": official_hit,
        "current_holder_signal": current_holder_signal,
        "examples": examples,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
