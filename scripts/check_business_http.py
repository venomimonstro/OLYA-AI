from __future__ import annotations

import asyncio
import re
import sys
from time import perf_counter
from urllib.parse import quote, quote_plus

import httpx


BLOCK_MARKERS = (
    "captcha",
    "smartcaptcha",
    "access denied",
    "unusual traffic",
    "проверка, что вы не робот",
    "доступ временно ограничен",
)


def city_slug(query: str) -> str:
    text = query.casefold()
    mapping = {
        "москва": "msk", "москве": "msk", "москвы": "msk",
        "санкт-петербург": "spb", "петербурге": "spb", "спб": "spb",
        "казань": "kazan", "казани": "kazan",
        "екатеринбург": "ekaterinburg", "екатеринбурге": "ekaterinburg",
        "новосибирск": "novosibirsk", "новосибирске": "novosibirsk",
    }
    for alias, slug in mapping.items():
        if alias in text:
            return slug
    return "msk"


def yell_city_slug(slug: str) -> str:
    return "moscow" if slug == "msk" else slug


def compact_query(query: str) -> str:
    value = re.sub(r"\b(?:лучши\w*|топ|рейтинг\w*|найди\w*|подбер\w*|посовет\w*)\b", " ", query, flags=re.I)
    return " ".join(value.split())[:220]


async def probe(client: httpx.AsyncClient, name: str, url: str) -> dict:
    started = perf_counter()
    try:
        response = await client.get(url)
        body = response.text[:2_000_000]
        low = body.casefold()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        title = " ".join(re.sub(r"<[^>]+>", " ", title_match.group(1)).split())[:160] if title_match else ""
        blocked = next((marker for marker in BLOCK_MARKERS if marker in low), "")
        return {
            "name": name,
            "requested": url,
            "status": response.status_code,
            "final": str(response.url),
            "bytes": len(response.content),
            "content_type": str(response.headers.get("content-type") or ""),
            "title": title,
            "blocked": blocked,
            "elapsed": perf_counter() - started,
            "error": "",
        }
    except Exception as exc:
        return {
            "name": name,
            "requested": url,
            "status": "ERROR",
            "final": "",
            "bytes": 0,
            "content_type": "",
            "title": "",
            "blocked": "",
            "elapsed": perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "лучшие автосервисы в москве"
    slug = city_slug(query)
    q = compact_query(query)
    zoon_root = "https://zoon.ru/" if slug == "msk" else f"https://zoon.ru/{slug}/"
    yell_root = f"https://www.yell.ru/{yell_city_slug(slug)}/"
    yandex_q = quote_plus(q)
    twogis_q = quote(q, safe="")

    urls = [
        ("zoon_root", zoon_root),
        ("yell_root", yell_root),
        ("yandex_maps", f"https://yandex.ru/maps/?text={yandex_q}"),
        ("2gis_search", f"https://2gis.ru/{'moscow' if slug == 'msk' else slug}/search/{twogis_q}"),
        ("searxng_internal", "http://searxng:8080/search?q=" + quote_plus(query) + "&format=json&language=ru&engines=yandex,duckduckgo,bing"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    timeout = httpx.Timeout(8.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False, headers=headers) as client:
        rows = await asyncio.gather(*(probe(client, name, url) for name, url in urls))

    print("query:", query)
    for row in rows:
        print("\n" + "=" * 80)
        print("provider:", row["name"])
        print("status:", row["status"])
        print("elapsed:", f"{row['elapsed']:.2f}s")
        print("bytes:", row["bytes"])
        print("content-type:", row["content_type"] or "-")
        print("final-url:", row["final"] or "-")
        print("title:", row["title"] or "-")
        print("blocked-marker:", row["blocked"] or "none")
        print("error:", row["error"] or "none")

    failed = sum(1 for row in rows if row["status"] == "ERROR")
    return 2 if failed == len(rows) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
