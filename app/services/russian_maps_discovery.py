from __future__ import annotations

import asyncio
import time
from urllib.parse import quote, quote_plus

import httpx

from app.services.public_maps_discovery import (
    MapPlace,
    _city_profile,
    _get,
    _json_payloads,
    _normalized_map_query,
    _twogis_from_json,
    _twogis_from_links,
    _yandex_from_json,
    _yandex_from_links,
)


_CACHE: dict[str, tuple[float, list[MapPlace]]] = {}
_CACHE_TTL = 10 * 60.0


class RussianMapsDiscovery:
    """Keyless bounded discovery from Yandex Maps and 2GIS public pages.

    Google Maps is intentionally excluded for the Russian deployment. CAPTCHA
    or blocked providers fail closed; no anti-bot bypassing is attempted.
    """

    def __init__(self, *, timeout_seconds: float = 4.0) -> None:
        self.timeout_seconds = max(1.5, min(float(timeout_seconds), 6.0))

    async def search(self, question: str) -> list[MapPlace]:
        key = " ".join(str(question or "").casefold().split())[:300]
        now = time.monotonic()
        cached = _CACHE.get(key)
        if cached and now - cached[0] <= _CACHE_TTL:
            return list(cached[1])

        city_slug, region_id, city_name = _city_profile(question)
        query = _normalized_map_query(question, city_name)
        encoded_path = quote(query, safe="")
        encoded_qs = quote_plus(query)
        urls = (
            ("yandex", f"https://yandex.ru/maps/{region_id}/{city_slug}/search/{encoded_path}/"),
            ("yandex", f"https://yandex.ru/maps/?text={encoded_qs}"),
            ("2gis", f"https://2gis.ru/{city_slug}/search/{encoded_path}"),
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(1.2, self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True, headers=headers) as client:
            async def fetch(provider: str, url: str) -> tuple[str, str, str]:
                try:
                    body = await _get(client, url)
                except (httpx.HTTPError, TimeoutError, ValueError):
                    return provider, url, ""
                low = body.casefold()
                if any(marker in low for marker in ("captcha", "smartcaptcha", "проверка, что вы не робот", "доступ временно ограничен")):
                    return provider, url, ""
                return provider, url, body

            responses = await asyncio.gather(*(fetch(provider, url) for provider, url in urls))

        rows: list[MapPlace] = []
        for provider, url, body in responses:
            if not body:
                continue
            payloads = _json_payloads(body)
            if provider == "yandex":
                found = _yandex_from_json(payloads, url) or _yandex_from_links(body, url)
            else:
                found = _twogis_from_json(payloads, city_slug, url) or _twogis_from_links(body, city_slug, url)
            rows.extend(found)

        deduped: list[MapPlace] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            marker = (row.provider, row.card_url)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(row)
            if len(deduped) >= 20:
                break
        _CACHE[key] = (now, list(deduped))
        return deduped
