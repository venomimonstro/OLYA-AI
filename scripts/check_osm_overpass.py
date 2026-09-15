from __future__ import annotations

import asyncio
import json
import sys
from time import perf_counter

import httpx

from app.services.osm_business_discovery import _ENDPOINTS, build_overpass_query


async def check(endpoint: str, query: str):
    started = perf_counter()
    headers = {
        "User-Agent": "OLYA-AI/1.0 (OpenStreetMap business lookup diagnostics)",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=2.5),
            follow_redirects=True,
            trust_env=False,
            headers=headers,
        ) as client:
            response = await client.post(endpoint, data={"data": query})
            body = response.text
            elapsed = perf_counter() - started
            payload = None
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                pass
            elements = payload.get("elements") if isinstance(payload, dict) else None
            remark = payload.get("remark") if isinstance(payload, dict) else None
            return {
                "endpoint": endpoint,
                "elapsed": elapsed,
                "status": response.status_code,
                "final_url": str(response.url),
                "bytes": len(response.content),
                "elements": len(elements) if isinstance(elements, list) else None,
                "remark": str(remark or "")[:500],
                "prefix": " ".join(body[:300].split()),
            }
    except Exception as exc:
        return {
            "endpoint": endpoint,
            "elapsed": perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main() -> int:
    question = " ".join(sys.argv[1:]).strip() or "лучшие автосервисы в москве"
    query = build_overpass_query(question)
    print("question:", question)
    print("\n--- overpass query ---")
    print(query or "<empty>")
    if not query:
        return 2

    results = await asyncio.gather(*(check(endpoint, query) for endpoint in _ENDPOINTS))
    ok = False
    for row in results:
        print("\n" + "=" * 72)
        print("endpoint:", row["endpoint"])
        print("elapsed:", f"{row['elapsed']:.2f}s")
        if row.get("error"):
            print("error:", row["error"])
            continue
        print("status:", row["status"])
        print("final_url:", row["final_url"])
        print("bytes:", row["bytes"])
        print("elements:", row["elements"])
        print("remark:", row["remark"] or "-")
        if row["status"] == 200 and isinstance(row["elements"], int) and row["elements"] > 0:
            ok = True
        if row["status"] != 200 or row["elements"] is None:
            print("body_prefix:", row["prefix"] or "-")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
