from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urlsplit


_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё_-]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: str) -> str:
    return _SPACE.sub(" ", str(value or "").casefold().replace("ё", "е")).strip()


def _fts_query(value: str) -> str:
    tokens = []
    for token in _WORD.findall(_norm(value)):
        token = token.strip("_- ")
        if len(token) < 2:
            continue
        escaped = token.replace('"', '""')
        tokens.append(f'"{escaped}"*')
    return " OR ".join(tokens[:12])


def _domain(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


@dataclass(frozen=True)
class BusinessHit:
    id: int
    name: str
    category: str
    subcategory: str
    country: str
    region: str
    city: str
    district: str
    address: str
    lat: float | None
    lon: float | None
    phone: str
    website: str
    opening_hours: str
    source: str
    source_url: str
    source_id: str
    confidence: float
    updated_at: str
    score: float


@dataclass(frozen=True)
class PageHit:
    id: int
    url: str
    domain: str
    title: str
    description: str
    content: str
    modified_at: str
    fetched_at: str
    score: float


class LocalSearchStore:
    """Low-RAM persistent search/index layer for OLYA.

    SQLite is intentional here: FTS5 provides BM25 full-text search, RTree keeps
    geo lookup cheap, WAL allows one background writer with concurrent chat
    readers, and the entire index lives on disk instead of competing with the
    LLM for RAM.
    """

    def __init__(self, path: str | None = None) -> None:
        configured = path or os.getenv("X1_LOCAL_SEARCH_DB") or "/app/data/olya_search.db"
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self.ensure_schema()
        connection = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-12000")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            if write:
                connection.execute("COMMIT")
        except Exception:
            if write:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            connection = sqlite3.connect(str(self.path), timeout=15.0)
            try:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    PRAGMA synchronous=NORMAL;
                    PRAGMA foreign_keys=ON;

                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS businesses (
                        id INTEGER PRIMARY KEY,
                        source_key TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        aliases TEXT NOT NULL DEFAULT '',
                        category TEXT NOT NULL DEFAULT '',
                        subcategory TEXT NOT NULL DEFAULT '',
                        country TEXT NOT NULL DEFAULT 'Россия',
                        region TEXT NOT NULL DEFAULT '',
                        city TEXT NOT NULL DEFAULT '',
                        district TEXT NOT NULL DEFAULT '',
                        street TEXT NOT NULL DEFAULT '',
                        house TEXT NOT NULL DEFAULT '',
                        address TEXT NOT NULL DEFAULT '',
                        lat REAL,
                        lon REAL,
                        phone TEXT NOT NULL DEFAULT '',
                        website TEXT NOT NULL DEFAULT '',
                        opening_hours TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL,
                        source_url TEXT NOT NULL DEFAULT '',
                        source_id TEXT NOT NULL DEFAULT '',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0.5,
                        stale_after TEXT NOT NULL DEFAULT ''
                    );

                    CREATE INDEX IF NOT EXISTS idx_business_city_category
                    ON businesses(city, category);
                    CREATE INDEX IF NOT EXISTS idx_business_updated
                    ON businesses(updated_at);
                    CREATE INDEX IF NOT EXISTS idx_business_source
                    ON businesses(source, source_id);

                    CREATE VIRTUAL TABLE IF NOT EXISTS business_fts USING fts5(
                        name,
                        aliases,
                        category,
                        subcategory,
                        city,
                        address,
                        tokenize='unicode61 remove_diacritics 2'
                    );

                    CREATE VIRTUAL TABLE IF NOT EXISTS business_geo USING rtree(
                        id,
                        min_lat, max_lat,
                        min_lon, max_lon
                    );

                    CREATE TABLE IF NOT EXISTS business_sources (
                        id INTEGER PRIMARY KEY,
                        business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                        source TEXT NOT NULL,
                        source_url TEXT NOT NULL DEFAULT '',
                        source_id TEXT NOT NULL DEFAULT '',
                        fetched_at TEXT NOT NULL,
                        payload_hash TEXT NOT NULL DEFAULT '',
                        UNIQUE(business_id, source, source_id, source_url)
                    );

                    CREATE TABLE IF NOT EXISTS pages (
                        id INTEGER PRIMARY KEY,
                        url TEXT NOT NULL UNIQUE,
                        domain TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        published_at TEXT NOT NULL DEFAULT '',
                        modified_at TEXT NOT NULL DEFAULT '',
                        fetched_at TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        status INTEGER NOT NULL DEFAULT 200,
                        stale_after TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain);
                    CREATE INDEX IF NOT EXISTS idx_pages_fetched ON pages(fetched_at);

                    CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
                        title,
                        description,
                        content,
                        domain,
                        tokenize='unicode61 remove_diacritics 2'
                    );

                    CREATE TABLE IF NOT EXISTS crawl_queue (
                        url TEXT PRIMARY KEY,
                        domain TEXT NOT NULL,
                        discovered_from TEXT NOT NULL DEFAULT '',
                        priority INTEGER NOT NULL DEFAULT 100,
                        next_fetch_at TEXT NOT NULL DEFAULT '',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_crawl_queue_ready
                    ON crawl_queue(priority, next_fetch_at);

                    CREATE TABLE IF NOT EXISTS domains (
                        domain TEXT PRIMARY KEY,
                        sitemap_url TEXT NOT NULL DEFAULT '',
                        robots_url TEXT NOT NULL DEFAULT '',
                        last_crawled_at TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()
            self._initialized = True

    def _business_source_key(self, record: dict) -> str:
        explicit = str(record.get("source_key") or "").strip()
        if explicit:
            return explicit[:500]
        source = str(record.get("source") or "local").strip().casefold()
        source_id = str(record.get("source_id") or "").strip()
        if source_id:
            return f"{source}:{source_id}"[:500]
        material = "|".join(
            [
                source,
                _norm(str(record.get("name") or "")),
                _norm(str(record.get("city") or "")),
                _norm(str(record.get("address") or "")),
                str(record.get("lat") or ""),
                str(record.get("lon") or ""),
            ]
        )
        return f"{source}:sha256:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    def upsert_business(self, record: dict) -> int:
        name = " ".join(str(record.get("name") or "").split()).strip()
        if len(name) < 2:
            raise ValueError("business name is required")
        now = _now_iso()
        source_key = self._business_source_key(record)
        aliases_value = record.get("aliases") or ""
        if isinstance(aliases_value, (list, tuple, set)):
            aliases = " | ".join(str(item).strip() for item in aliases_value if str(item).strip())
        else:
            aliases = str(aliases_value or "")
        values = {
            "source_key": source_key,
            "name": name[:240],
            "normalized_name": _norm(name)[:240],
            "aliases": aliases[:1000],
            "category": str(record.get("category") or "")[:120],
            "subcategory": str(record.get("subcategory") or "")[:120],
            "country": str(record.get("country") or "Россия")[:120],
            "region": str(record.get("region") or "")[:180],
            "city": str(record.get("city") or "")[:180],
            "district": str(record.get("district") or "")[:180],
            "street": str(record.get("street") or "")[:180],
            "house": str(record.get("house") or "")[:80],
            "address": str(record.get("address") or "")[:500],
            "lat": record.get("lat"),
            "lon": record.get("lon"),
            "phone": str(record.get("phone") or "")[:180],
            "website": str(record.get("website") or "")[:500],
            "opening_hours": str(record.get("opening_hours") or "")[:500],
            "source": str(record.get("source") or "local")[:80],
            "source_url": str(record.get("source_url") or "")[:1000],
            "source_id": str(record.get("source_id") or "")[:240],
            "last_seen_at": str(record.get("last_seen_at") or now),
            "updated_at": str(record.get("updated_at") or now),
            "confidence": max(0.0, min(float(record.get("confidence") or 0.5), 1.0)),
            "stale_after": str(record.get("stale_after") or ""),
        }
        with self._connect(write=True) as connection:
            connection.execute(
                """
                INSERT INTO businesses (
                    source_key,name,normalized_name,aliases,category,subcategory,country,region,city,district,
                    street,house,address,lat,lon,phone,website,opening_hours,source,source_url,source_id,
                    first_seen_at,last_seen_at,updated_at,confidence,stale_after
                ) VALUES (
                    :source_key,:name,:normalized_name,:aliases,:category,:subcategory,:country,:region,:city,:district,
                    :street,:house,:address,:lat,:lon,:phone,:website,:opening_hours,:source,:source_url,:source_id,
                    :updated_at,:last_seen_at,:updated_at,:confidence,:stale_after
                )
                ON CONFLICT(source_key) DO UPDATE SET
                    name=excluded.name,
                    normalized_name=excluded.normalized_name,
                    aliases=CASE WHEN excluded.aliases!='' THEN excluded.aliases ELSE businesses.aliases END,
                    category=CASE WHEN excluded.category!='' THEN excluded.category ELSE businesses.category END,
                    subcategory=CASE WHEN excluded.subcategory!='' THEN excluded.subcategory ELSE businesses.subcategory END,
                    country=excluded.country,
                    region=CASE WHEN excluded.region!='' THEN excluded.region ELSE businesses.region END,
                    city=CASE WHEN excluded.city!='' THEN excluded.city ELSE businesses.city END,
                    district=CASE WHEN excluded.district!='' THEN excluded.district ELSE businesses.district END,
                    street=CASE WHEN excluded.street!='' THEN excluded.street ELSE businesses.street END,
                    house=CASE WHEN excluded.house!='' THEN excluded.house ELSE businesses.house END,
                    address=CASE WHEN excluded.address!='' THEN excluded.address ELSE businesses.address END,
                    lat=COALESCE(excluded.lat,businesses.lat),
                    lon=COALESCE(excluded.lon,businesses.lon),
                    phone=CASE WHEN excluded.phone!='' THEN excluded.phone ELSE businesses.phone END,
                    website=CASE WHEN excluded.website!='' THEN excluded.website ELSE businesses.website END,
                    opening_hours=CASE WHEN excluded.opening_hours!='' THEN excluded.opening_hours ELSE businesses.opening_hours END,
                    source_url=CASE WHEN excluded.source_url!='' THEN excluded.source_url ELSE businesses.source_url END,
                    source_id=CASE WHEN excluded.source_id!='' THEN excluded.source_id ELSE businesses.source_id END,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at,
                    confidence=MAX(businesses.confidence,excluded.confidence),
                    stale_after=CASE WHEN excluded.stale_after!='' THEN excluded.stale_after ELSE businesses.stale_after END
                """,
                values,
            )
            row = connection.execute("SELECT id FROM businesses WHERE source_key=?", (source_key,)).fetchone()
            business_id = int(row["id"])
            connection.execute("DELETE FROM business_fts WHERE rowid=?", (business_id,))
            connection.execute(
                "INSERT INTO business_fts(rowid,name,aliases,category,subcategory,city,address) VALUES (?,?,?,?,?,?,?)",
                (
                    business_id,
                    values["name"],
                    values["aliases"],
                    values["category"],
                    values["subcategory"],
                    values["city"],
                    values["address"],
                ),
            )
            connection.execute("DELETE FROM business_geo WHERE id=?", (business_id,))
            try:
                lat = float(values["lat"]) if values["lat"] is not None else None
                lon = float(values["lon"]) if values["lon"] is not None else None
            except (TypeError, ValueError):
                lat = lon = None
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                connection.execute(
                    "INSERT INTO business_geo(id,min_lat,max_lat,min_lon,max_lon) VALUES (?,?,?,?,?)",
                    (business_id, lat, lat, lon, lon),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO business_sources(business_id,source,source_url,source_id,fetched_at,payload_hash)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    business_id,
                    values["source"],
                    values["source_url"],
                    values["source_id"],
                    now,
                    str(record.get("payload_hash") or "")[:128],
                ),
            )
            return business_id

    def search_businesses(self, query: str, *, city: str = "", category: str = "", limit: int = 10) -> list[BusinessHit]:
        limit = max(1, min(int(limit), 50))
        fts = _fts_query(query)
        city_norm = _norm(city)
        category_norm = _norm(category)
        clauses = []
        params: list[object] = []
        if city_norm:
            clauses.append("lower(replace(b.city,'Ё','Е')) LIKE ?")
            params.append(f"%{city_norm}%")
        if category_norm:
            clauses.append("(lower(b.category) LIKE ? OR lower(b.subcategory) LIKE ?)")
            params.extend((f"%{category_norm}%", f"%{category_norm}%"))
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as connection:
            if fts:
                rows = connection.execute(
                    f"""
                    SELECT b.*, bm25(business_fts, 6.0, 2.0, 4.0, 2.0, 3.0, 1.0) AS rank
                    FROM business_fts
                    JOIN businesses b ON b.id=business_fts.rowid
                    WHERE business_fts MATCH ? {where}
                    ORDER BY rank ASC, b.confidence DESC, b.updated_at DESC
                    LIMIT ?
                    """,
                    [fts, *params, limit],
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT b.*, 0.0 AS rank
                    FROM businesses b
                    WHERE 1=1 {where}
                    ORDER BY b.confidence DESC, b.updated_at DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
        return [self._business_hit(row) for row in rows]

    def nearby(self, *, lat: float, lon: float, radius_km: float = 5.0, category: str = "", limit: int = 20) -> list[BusinessHit]:
        radius_km = max(0.1, min(float(radius_km), 100.0))
        lat_delta = radius_km / 111.0
        lon_scale = max(0.15, abs(__import__("math").cos(__import__("math").radians(lat))))
        lon_delta = radius_km / (111.0 * lon_scale)
        params: list[object] = [lat + lat_delta, lat - lat_delta, lon + lon_delta, lon - lon_delta]
        category_sql = ""
        if category:
            category_sql = " AND (lower(b.category) LIKE ? OR lower(b.subcategory) LIKE ?)"
            value = f"%{_norm(category)}%"
            params.extend((value, value))
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT b.*, ((b.lat-?)*(b.lat-?) + (b.lon-?)*(b.lon-?)) AS rank
                FROM business_geo g JOIN businesses b ON b.id=g.id
                WHERE g.min_lat<=? AND g.max_lat>=? AND g.min_lon<=? AND g.max_lon>=? {category_sql}
                ORDER BY rank ASC, b.confidence DESC
                LIMIT ?
                """.replace(
                    "SELECT b.*, ((b.lat-?)*(b.lat-?) + (b.lon-?)*(b.lon-?)) AS rank",
                    "SELECT b.*, ((b.lat-{lat})*(b.lat-{lat}) + (b.lon-{lon})*(b.lon-{lon})) AS rank".format(lat=float(lat), lon=float(lon)),
                ),
                params,
            ).fetchall()
        return [self._business_hit(row) for row in rows]

    def _business_hit(self, row: sqlite3.Row) -> BusinessHit:
        return BusinessHit(
            id=int(row["id"]),
            name=str(row["name"]),
            category=str(row["category"]),
            subcategory=str(row["subcategory"]),
            country=str(row["country"]),
            region=str(row["region"]),
            city=str(row["city"]),
            district=str(row["district"]),
            address=str(row["address"]),
            lat=float(row["lat"]) if row["lat"] is not None else None,
            lon=float(row["lon"]) if row["lon"] is not None else None,
            phone=str(row["phone"]),
            website=str(row["website"]),
            opening_hours=str(row["opening_hours"]),
            source=str(row["source"]),
            source_url=str(row["source_url"]),
            source_id=str(row["source_id"]),
            confidence=float(row["confidence"]),
            updated_at=str(row["updated_at"]),
            score=float(row["rank"] or 0.0),
        )

    def upsert_page(self, *, url: str, title: str = "", description: str = "", content: str = "", published_at: str = "", modified_at: str = "", status: int = 200, stale_after: str = "") -> int:
        canonical = str(url or "").strip()
        domain = _domain(canonical)
        if not canonical.startswith(("http://", "https://")) or not domain:
            raise ValueError("absolute http(s) url is required")
        clean_content = " ".join(str(content or "").split())[:1_500_000]
        payload = "\n".join((title, description, clean_content))
        digest = hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()
        now = _now_iso()
        with self._connect(write=True) as connection:
            connection.execute(
                """
                INSERT INTO pages(url,domain,title,description,content,published_at,modified_at,fetched_at,content_hash,status,stale_after)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET
                    domain=excluded.domain,title=excluded.title,description=excluded.description,content=excluded.content,
                    published_at=CASE WHEN excluded.published_at!='' THEN excluded.published_at ELSE pages.published_at END,
                    modified_at=CASE WHEN excluded.modified_at!='' THEN excluded.modified_at ELSE pages.modified_at END,
                    fetched_at=excluded.fetched_at,content_hash=excluded.content_hash,status=excluded.status,stale_after=excluded.stale_after
                """,
                (canonical, domain, title[:500], description[:1500], clean_content, published_at[:80], modified_at[:80], now, digest, int(status), stale_after[:80]),
            )
            row = connection.execute("SELECT id FROM pages WHERE url=?", (canonical,)).fetchone()
            page_id = int(row["id"])
            connection.execute("DELETE FROM page_fts WHERE rowid=?", (page_id,))
            connection.execute(
                "INSERT INTO page_fts(rowid,title,description,content,domain) VALUES (?,?,?,?,?)",
                (page_id, title[:500], description[:1500], clean_content, domain),
            )
            return page_id

    def search_pages(self, query: str, *, domains: Iterable[str] = (), limit: int = 8) -> list[PageHit]:
        fts = _fts_query(query)
        if not fts:
            return []
        domain_values = [str(value).casefold().removeprefix("www.") for value in domains if str(value).strip()]
        domain_sql = ""
        params: list[object] = [fts]
        if domain_values:
            placeholders = ",".join("?" for _ in domain_values)
            domain_sql = f" AND p.domain IN ({placeholders})"
            params.extend(domain_values)
        params.append(max(1, min(int(limit), 50)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, bm25(page_fts, 5.0, 2.0, 1.0, 1.0) AS rank
                FROM page_fts JOIN pages p ON p.id=page_fts.rowid
                WHERE page_fts MATCH ? {domain_sql}
                ORDER BY rank ASC, p.fetched_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            PageHit(
                id=int(row["id"]),
                url=str(row["url"]),
                domain=str(row["domain"]),
                title=str(row["title"]),
                description=str(row["description"]),
                content=str(row["content"]),
                modified_at=str(row["modified_at"]),
                fetched_at=str(row["fetched_at"]),
                score=float(row["rank"] or 0.0),
            )
            for row in rows
        ]

    def enqueue(self, url: str, *, discovered_from: str = "", priority: int = 100, next_fetch_at: str = "") -> None:
        canonical = str(url or "").strip()
        domain = _domain(canonical)
        if not canonical.startswith(("http://", "https://")) or not domain:
            return
        with self._connect(write=True) as connection:
            connection.execute(
                """
                INSERT INTO crawl_queue(url,domain,discovered_from,priority,next_fetch_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET priority=MIN(crawl_queue.priority,excluded.priority)
                """,
                (canonical, domain, discovered_from[:1000], int(priority), next_fetch_at[:80]),
            )

    def queue_batch(self, limit: int = 20) -> list[dict]:
        now = _now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM crawl_queue
                WHERE next_fetch_at='' OR next_fetch_at<=?
                ORDER BY priority ASC, attempts ASC
                LIMIT ?
                """,
                (now, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_done(self, url: str) -> None:
        with self._connect(write=True) as connection:
            connection.execute("DELETE FROM crawl_queue WHERE url=?", (url,))

    def queue_failed(self, url: str, error: str, *, next_fetch_at: str = "") -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "UPDATE crawl_queue SET attempts=attempts+1,last_error=?,next_fetch_at=? WHERE url=?",
                (str(error)[:500], next_fetch_at[:80], url),
            )

    def stats(self) -> dict[str, int | str]:
        with self._connect() as connection:
            businesses = int(connection.execute("SELECT COUNT(*) FROM businesses").fetchone()[0])
            pages = int(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
            queued = int(connection.execute("SELECT COUNT(*) FROM crawl_queue").fetchone()[0])
        return {"businesses": businesses, "pages": pages, "queued": queued, "path": str(self.path)}


_STORE: LocalSearchStore | None = None
_STORE_LOCK = threading.Lock()


def get_local_search_store() -> LocalSearchStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = LocalSearchStore()
                _STORE.ensure_schema()
    return _STORE
