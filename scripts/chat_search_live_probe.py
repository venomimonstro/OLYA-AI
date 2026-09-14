#!/usr/bin/env python3
from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen


def _json(url: str, timeout: float) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def main() -> int:
    report: dict = {
        "format": "olya-chat-search-live-probe-v1",
        "llama": {"ok": False},
        "search": {"ok": False, "results": 0, "examples": []},
    }

    try:
        with urlopen("http://llama:8080/health", timeout=8) as response:
            body = response.read().decode("utf-8", "replace")
        report["llama"] = {"ok": True, "health": body[:300]}
    except Exception as exc:
        report["llama"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}

    query = "Qwen AI"
    params = urlencode({"q": query, "format": "json", "language": "ru-RU", "safesearch": "1"})
    try:
        payload = _json(f"http://searxng:8080/search?{params}", 15)
        rows = list(payload.get("results") or [])
        examples = []
        for row in rows[:3]:
            examples.append({
                "title": str(row.get("title") or "")[:160],
                "url": str(row.get("url") or "")[:500],
                "engine": str(row.get("engine") or "")[:80],
            })
        report["search"] = {
            "ok": bool(rows),
            "query": query,
            "results": len(rows),
            "examples": examples,
        }
        if not rows:
            report["search"]["error"] = "SearXNG responded but no external search results were returned"
    except Exception as exc:
        report["search"] = {
            "ok": False,
            "query": query,
            "results": 0,
            "examples": [],
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }

    report["status"] = "passed" if report["llama"]["ok"] and report["search"]["ok"] else "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
