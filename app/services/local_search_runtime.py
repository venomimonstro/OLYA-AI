from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from app.services.local_search_store import LocalSearchStore, get_local_search_store
from app.services.local_web_crawler import LocalWebCrawler

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().casefold()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _business_roots(store: LocalSearchStore, *, limit: int) -> list[str]:
    """Return a small rotating set of official business-site roots.

    The database stores only business facts; crawling starts from websites that
    were already observed in OSM or another source. This avoids inventing
    domains and keeps the maintenance worker focused and cheap.
    """
    connection = sqlite3.connect(str(store.path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT website
            FROM businesses
            WHERE website != ''
            ORDER BY COALESCE(last_seen_at, updated_at) ASC, confidence DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    finally:
        connection.close()

    roots: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = str(row["website"] or "").strip()
        if raw.startswith("www."):
            raw = "https://" + raw
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host or host in seen:
            continue
        seen.add(host)
        roots.append(f"{parsed.scheme}://{parsed.netloc}/")
    return roots


def _file_roots(path: Path, *, limit: int) -> list[str]:
    if not path.is_file():
        return []
    rows: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        rows.append(value)
        if len(rows) >= limit:
            break
    return rows


async def owned_search_maintenance_loop() -> None:
    """Incrementally maintain OLYA's own page index at very low resource cost.

    This loop never downloads the Russia PBF. Bulk geo bootstrap is an explicit
    one-time operation. At runtime we only seed/crawl a few text pages and keep
    using the last local copy when external sites are unavailable.
    """
    if not _bool_env("X1_OWNED_SEARCH_MAINTENANCE_ENABLED", True):
        return

    store = get_local_search_store()
    store.ensure_schema()
    crawler = LocalWebCrawler()
    interval = _int_env("X1_OWNED_SEARCH_INTERVAL_SECONDS", 900, 120, 86_400)
    crawl_limit = _int_env("X1_OWNED_SEARCH_CRAWL_LIMIT", 8, 1, 60)
    seed_limit = _int_env("X1_OWNED_SEARCH_SEED_LIMIT", 4, 0, 30)
    max_urls = _int_env("X1_OWNED_SEARCH_MAX_URLS_PER_DOMAIN", 250, 20, 5_000)
    seeds_file = Path(os.getenv("X1_OWNED_SEARCH_SEEDS_FILE", "/app/data/search-seeds.txt"))
    last_seed_at = 0.0

    while True:
        try:
            stats = store.stats()
            queued = int(stats.get("queued") or 0)
            businesses = int(stats.get("businesses") or 0)
            loop_now = asyncio.get_running_loop().time()

            # Seed infrequently and only when the queue is running low. This
            # avoids hammering sitemaps and keeps crawler CPU/network negligible.
            if seed_limit > 0 and queued < max(10, crawl_limit * 2) and loop_now - last_seed_at >= 6 * 3600:
                roots: list[str] = []
                roots.extend(_file_roots(seeds_file, limit=seed_limit))
                if businesses > 0 and len(roots) < seed_limit:
                    roots.extend(_business_roots(store, limit=seed_limit - len(roots)))
                seen: set[str] = set()
                for root in roots:
                    host = (urlsplit(root).hostname or "").casefold().removeprefix("www.")
                    if not host or host in seen:
                        continue
                    seen.add(host)
                    try:
                        await crawler.seed_domain(root, max_sitemaps=8, max_urls=max_urls)
                    except Exception as exc:
                        logger.debug("Owned search seed failed for %s: %s", root, exc)
                last_seed_at = loop_now

            if int(store.stats().get("queued") or 0) > 0:
                await crawler.crawl_batch(limit=crawl_limit, concurrency=1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Owned search maintenance cycle failed")

        await asyncio.sleep(interval)
