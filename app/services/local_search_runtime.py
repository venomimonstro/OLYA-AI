from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
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
    """Return least-recently-seeded official business website roots."""
    connection = sqlite3.connect(str(store.path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        candidates = connection.execute(
            """
            SELECT website
            FROM businesses
            WHERE website != ''
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (max(50, min(int(limit) * 100, 20_000)),),
        ).fetchall()
        domain_rows = connection.execute("SELECT domain,last_crawled_at FROM domains").fetchall()
    finally:
        connection.close()

    last_seeded = {str(row["domain"]): str(row["last_crawled_at"] or "") for row in domain_rows}
    choices: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in candidates:
        raw = str(row["website"] or "").strip()
        if raw.startswith("www."):
            raw = "https://" + raw
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host or host in seen:
            continue
        seen.add(host)
        choices.append((last_seeded.get(host, ""), host, f"{parsed.scheme}://{parsed.netloc}/"))
    choices.sort(key=lambda item: (bool(item[0]), item[0], item[1]))
    return [root for _last, _host, root in choices[: max(1, limit)]]


def _mark_domain_attempt(store: LocalSearchStore, root: str) -> None:
    parsed = urlsplit(root)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not host:
        return
    now = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(str(store.path), timeout=10.0)
    try:
        connection.execute(
            """
            INSERT INTO domains(domain,last_crawled_at,enabled)
            VALUES (?,?,1)
            ON CONFLICT(domain) DO UPDATE SET last_crawled_at=excluded.last_crawled_at
            """,
            (host, now),
        )
        connection.commit()
    finally:
        connection.close()


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

            # Seed infrequently and only when the queue is running low. Domains
            # are least-recently-seeded, so the crawler expands coverage over
            # time instead of repeatedly hitting the same few sites.
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
                    finally:
                        # Mark attempts too; an unavailable site must not starve
                        # thousands of other domains from ever being indexed.
                        _mark_domain_attempt(store, root)
                last_seed_at = loop_now

            if int(store.stats().get("queued") or 0) > 0:
                await crawler.crawl_batch(limit=crawl_limit, concurrency=1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Owned search maintenance cycle failed")

        await asyncio.sleep(interval)
