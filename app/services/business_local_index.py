from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from threading import RLock

from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question

_DATA_ROOT = Path(os.getenv("X1_DATA_ROOT", "/app/data"))
_DB_PATH = _DATA_ROOT / "business_index.sqlite3"
_LOCK = RLock()

_CITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bмоскв\w*\b", re.I), "москва"),
    (re.compile(r"\b(?:санкт[-\s]?петербург\w*|петербург\w*|спб)\b", re.I), "санкт-петербург"),
    (re.compile(r"\bказан\w*\b", re.I), "казань"),
    (re.compile(r"\bекатеринбург\w*\b", re.I), "екатеринбург"),
    (re.compile(r"\bновосибирск\w*\b", re.I), "новосибирск"),
    (re.compile(r"\bсамар\w*\b", re.I), "самара"),
    (re.compile(r"\bчелябинск\w*\b", re.I), "челябинск"),
    (re.compile(r"\bкрасноярск\w*\b", re.I), "красноярск"),
    (re.compile(r"\bтюмен\w*\b", re.I), "тюмень"),
    (re.compile(r"\bуф\w*\b", re.I), "уфа"),
    (re.compile(r"\bперм\w*\b", re.I), "пермь"),
    (re.compile(r"\bсочи\b", re.I), "сочи"),
    (re.compile(r"\bворонеж\w*\b", re.I), "воронеж"),
    (re.compile(r"\bкраснодар\w*\b", re.I), "краснодар"),
    (re.compile(r"\bомск\w*\b", re.I), "омск"),
)

_CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"автосервис|авторемонт|ремонт\w*\s+(?:авто|машин)|\bсто\b", re.I), "автосервис"),
    (re.compile(r"слухопротез|слухов\w*\s+аппарат|сурдолог|аудиолог", re.I), "слухопротезирование"),
    (re.compile(r"стоматолог", re.I), "стоматология"),
    (re.compile(r"\bмедицина\b|клиник|медицин\w*\s+центр|диагност\w*\s+центр", re.I), "медицина"),
    (re.compile(r"юрист|адвокат|юридичес", re.I), "юристы"),
    (re.compile(r"ресторан", re.I), "рестораны"),
    (re.compile(r"кафе|кофейн", re.I), "кафе"),
    (re.compile(r"фитнес|спортзал|тренаж", re.I), "фитнес"),
    (re.compile(r"\bкрасота\b|салон\w*\s+красот|косметолог|маникюр|педикюр", re.I), "красота"),
    (re.compile(r"парикмах|барбершоп", re.I), "парикмахерские"),
    (re.compile(r"аптек", re.I), "аптеки"),
    (re.compile(r"ветеринар|ветклиник", re.I), "ветеринария"),
    (re.compile(r"риелтор|риэлтор|недвижим", re.I), "недвижимость"),
    (re.compile(r"шиномонтаж|шины|покрышк", re.I), "шиномонтаж"),
    (re.compile(r"автомойк", re.I), "автомойка"),
    (re.compile(r"химчист", re.I), "химчистка"),
    (re.compile(r"отел|гостиниц", re.I), "гостиницы"),
)


def query_scope(question: str) -> tuple[str, str]:
    text = normalized_question(question)
    city = next((name for pattern, name in _CITY_PATTERNS if pattern.search(text)), "москва")
    category = next((name for pattern, name in _CATEGORY_PATTERNS if pattern.search(text)), "")
    return city, category


def _connect() -> sqlite3.Connection:
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS business_places (
            city TEXT NOT NULL,
            category TEXT NOT NULL,
            provider TEXT NOT NULL,
            name TEXT NOT NULL,
            card_url TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            rating REAL,
            reviews INTEGER,
            source_url TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (city, category, provider, card_url)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_business_scope ON business_places(city, category)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_business_rank ON business_places(city, category, rating DESC, reviews DESC)")
    return db


def _mirror_unified(question: str, places: list[MapPlace]) -> None:
    try:
        from app.services.local_business_index import persist_places
        persist_places(question, places)
    except Exception:
        # Compatibility cache writes must remain non-fatal even if the newer
        # unified search store is unavailable/corrupt during an upgrade.
        return


def store_places(question: str, places: list[MapPlace]) -> None:
    if not places:
        return
    city, category = query_scope(question)
    if not category:
        return
    now = int(time.time())
    payload = []
    for place in places:
        if not place.name or not place.card_url:
            continue
        payload.append((city, category, place.provider, place.name, place.card_url, place.address, place.phone, place.website, place.rating, place.reviews, place.source_url, now))
    if not payload:
        return
    with _LOCK:
        with _connect() as db:
            db.executemany(
                """
                INSERT INTO business_places(city,category,provider,name,card_url,address,phone,website,rating,reviews,source_url,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(city,category,provider,card_url) DO UPDATE SET
                    name=excluded.name,
                    address=CASE WHEN excluded.address<>'' THEN excluded.address ELSE business_places.address END,
                    phone=CASE WHEN excluded.phone<>'' THEN excluded.phone ELSE business_places.phone END,
                    website=CASE WHEN excluded.website<>'' THEN excluded.website ELSE business_places.website END,
                    rating=COALESCE(excluded.rating,business_places.rating),
                    reviews=COALESCE(excluded.reviews,business_places.reviews),
                    source_url=CASE WHEN excluded.source_url<>'' THEN excluded.source_url ELSE business_places.source_url END,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
    _mirror_unified(question, places)


def load_places(question: str, *, limit: int = 20) -> list[MapPlace]:
    city, category = query_scope(question)
    if not category:
        return []
    with _LOCK:
        with _connect() as db:
            rows = db.execute(
                """
                SELECT provider,name,card_url,address,phone,website,rating,reviews,source_url
                FROM business_places
                WHERE city=? AND category=?
                ORDER BY (rating IS NOT NULL) DESC, rating DESC, COALESCE(reviews,0) DESC, updated_at DESC
                LIMIT ?
                """,
                (city, category, max(1, int(limit))),
            ).fetchall()
    return [
        MapPlace(provider=row[0], name=row[1], card_url=row[2], address=row[3], phone=row[4], website=row[5], rating=row[6], reviews=row[7], source_url=row[8])
        for row in rows
    ]


def index_stats() -> dict[str, int]:
    with _LOCK:
        with _connect() as db:
            rows = int(db.execute("SELECT COUNT(*) FROM business_places").fetchone()[0])
            scopes = int(db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT city,category FROM business_places)").fetchone()[0])
    return {"rows": rows, "scopes": scopes}


def clear_scope(question: str) -> int:
    city, category = query_scope(question)
    if not category:
        return 0
    with _LOCK:
        with _connect() as db:
            cursor = db.execute("DELETE FROM business_places WHERE city=? AND category=?", (city, category))
            return int(cursor.rowcount or 0)
