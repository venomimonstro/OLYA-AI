from __future__ import annotations

import httpx

from app.services.discovery import DiscoveryError, SearchHit


class SearxngDiscovery:
    """Internal no-key search discovery through the X1 SearXNG sidecar."""

    name = "searxng"

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(2.0, float(timeout_seconds))

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
        params: dict[str, object] = {
            "q": query,
            "format": "json",
            "safesearch": 1,
            "pageno": 1,
        }
        if language:
            params["language"] = language
        # SearXNG does not have one universal country parameter across all
        # engines. Keep the locality signal in the query/planner instead of
        # fabricating engine-specific parameters.
        _ = country
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/search", params=params, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DiscoveryError("Self-hosted search provider request failed") from exc

        hits: list[SearchHit] = []
        for index, row in enumerate(payload.get("results") or [], start=1):
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
                    provider=self.name,
                )
            )
            if len(hits) >= min(max(int(count), 1), 20):
                break
        return hits
