from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock

from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question

_DATA_ROOT = Path(os.getenv("X1_DATA_ROOT", "/app/data"))
_INDEX_PATH = _DATA_ROOT / "business_index.json"
_LOCK = RLock()
_MAX_ROWS = 20_000

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
    (re.compile(r"клиник|медицин\w*\s+центр|диагност\w*\s+центр", re.I), "медицина"),
    (re.compile(r"юрист|адвокат|юридичес", re.I), "юристы"),
    (re.compile(r"ресторан", re.I), "рестораны"),
    (re.compile(r"кафе|кофейн", re.I), "кафе"),
    (re.compile(r"фитнес|спортзал|тренаж", re.I), "фитнес"),
    (re.compile(r"салон\w*\s+красот|косметолог|маникюр|педикюр", re.I), "красота"),
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


def _load() -> list[dict]:
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _save(rows: list[dict]) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows[-_MAX_ROWS:], ensure_ascii=False, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(prefix="business_index_", suffix=".json", dir=str(_INDEX_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, _INDEX_PATH)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def store_places(question: str, places: list[MapPlace]) -> None:
    if not places:
        return
    city, category = query_scope(question)
    now = int(time.time())
    with _LOCK:
        rows = _load()
        keyed = {(str(row.get("city")), str(row.get("category")), str(row.get("provider")), str(row.get("card_url"))): row for row in rows}
        for place in places:
            if not place.name or not place.card_url:
                continue
            key = (city, category, place.provider, place.card_url)
            keyed[key] = {
                "city": city,
                "category": category,
                "provider": place.provider,
                "name": place.name,
                "card_url": place.card_url,
                "address": place.address,
                "phone": place.phone,
                "website": place.website,
                "rating": place.rating,
                "reviews": place.reviews,
                "source_url": place.source_url,
                "updated_at": now,
            }
        compact = sorted(keyed.values(), key=lambda row: int(row.get("updated_at") or 0))[-_MAX_ROWS:]
        _save(compact)


def load_places(question: str, *, limit: int = 20) -> list[MapPlace]:
    city, category = query_scope(question)
    if not category:
        return []
    with _LOCK:
        rows = _load()
    matches = [row for row in rows if row.get("city") == city and row.get("category") == category]
    matches.sort(key=lambda row: (float(row.get("rating") or 0), int(row.get("reviews") or 0), int(row.get("updated_at") or 0)), reverse=True)
    result: list[MapPlace] = []
    for row in matches[: max(1, limit)]:
        result.append(MapPlace(
            provider=str(row.get("provider") or "cache"),
            name=str(row.get("name") or ""),
            card_url=str(row.get("card_url") or ""),
            address=str(row.get("address") or ""),
            phone=str(row.get("phone") or ""),
            website=str(row.get("website") or ""),
            rating=float(row["rating"]) if isinstance(row.get("rating"), (int, float)) else None,
            reviews=int(row["reviews"]) if isinstance(row.get("reviews"), int) else None,
            source_url=str(row.get("source_url") or row.get("card_url") or ""),
        ))
    return result


def index_stats() -> dict[str, int]:
    with _LOCK:
        rows = _load()
    scopes = {(str(row.get("city")), str(row.get("category"))) for row in rows}
    return {"rows": len(rows), "scopes": len(scopes)}
