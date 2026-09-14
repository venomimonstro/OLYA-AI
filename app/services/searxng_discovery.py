from __future__ import annotations

import asyncio

import httpx

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url


class SearxngDiscovery:
    """Internal no-key metasearch through the OLYA SearXNG sidecar."""

    name = "searxng"
    # Large engines plus independent/free alternatives. Requests are issued per
    # engine in parallel so one CAPTCHA/slow engine cannot hold the whole answer.
    general_engines = (
        "google",
        "yandex",
        "bing",
        "duckduckgo",
        "brave",
        "startpage",
        "qwant",
    )

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(2.0, float(timeout_seconds))

    @staticmethod
    def _merge_hits(per_engine: dict[str, list[SearchHit]], *, limit: int) -> list[SearchHit]:
        by_url: dict[str, SearchHit] = {}
        providers: dict[str, list[str]] = {}
        order: list[str] = []
        # Round-robin keeps one prolific engine from dominating the first page.
        max_len = max((len(rows) for rows in per_engine.values()), default=0)
        engines = [name for name in SearxngDiscovery.general_engines if name in per_engine]
        for index in range(max_len):
            for engine in engines:
                rows = per_engine.get(engine) or []
                if index >= len(rows):
                    continue
                hit = rows[index]
                key = canonical_result_url(hit.url)
                if not key:
                    continue
                if key not in by_url:
                    by_url[key] = hit
                    providers[key] = []
                    order.append(key)
                if engine not in providers[key]:
                    providers[key].append(engine)
        result: list[SearchHit] = []
        for key in order:
            base = by_url[key]
            source_engines = providers.get(key) or []
            result.append(
                SearchHit(
                    query=base.query,
                    title=base.title,
                    url=base.url,
                    snippet=base.snippet,
                    rank=len(result) + 1,
                    provider="searxng:" + ",".join(source_engines[:4]) if source_engines else "searxng",
                )
            )
            if len(result) >= limit:
                break
        return result

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchHit]:
        if not self.base_url:
            raise DiscoveryError("SearXNG discovery is not configured")
        _ = country
        count = min(max(int(count), 1), 20)
        loop = asyncio.get_running_loop()
        global_budget = min(self.timeout_seconds, 5.5)
        per_engine_timeout = min(self.timeout_seconds, 4.5)
        deadline = loop.time() + global_budget
        results: dict[str, list[SearchHit]] = {}

        async with httpx.AsyncClient(timeout=per_engine_timeout, trust_env=False) as client:
            async def one(engine: str) -> tuple[str, list[SearchHit]]:
                params: dict[str, object] = {
                    "q": query,
                    "format": "json",
                    "safesearch": 1,
                    "pageno": 1,
                    "categories": "general",
                    "engines": engine,
                }
                if language:
                    params["language"] = language
                try:
                    response = await client.get(
                        f"{self.base_url}/search",
                        params=params,
                        headers={"Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    return engine, []
                hits: list[SearchHit] = []
                for index, row in enumerate(payload.get("results") or [], start=1):
                    if not isinstance(row, dict):
                        continue
                    url = str(row.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        continue
                    hits.append(
                        SearchHit(
                            query=query,
                            title=str(row.get("title") or "")[:500],
                            url=url,
                            snippet=str(row.get("content") or row.get("snippet") or "")[:2000],
                            rank=index,
                            provider=f"searxng:{engine}",
                        )
                    )
                    if len(hits) >= count:
                        break
                return engine, hits

            pending = {asyncio.create_task(one(engine)) for engine in self.general_engines}
            try:
                while pending:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    done, pending = await asyncio.wait(
                        pending,
                        timeout=min(0.65, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        try:
                            engine, hits = task.result()
                        except Exception:
                            continue
                        if hits:
                            results[engine] = hits
                    merged = self._merge_hits(results, limit=count)
                    # Two independent engines and a useful result set are enough
                    # for interactive chat. Do not wait for CAPTCHA-bound engines.
                    if len(results) >= 2 and len(merged) >= min(count, 6):
                        break
                    if len(results) >= 3 and merged:
                        break
            finally:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        merged = self._merge_hits(results, limit=count)
        if not merged:
            raise DiscoveryError("Self-hosted search providers returned no results")
        return merged
