from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from app.services.business_quality import (
    ensure_business_quality_schema,
    load_business_quality,
    quality_score,
)
from app.services.local_search_store import BusinessHit, LocalSearchStore, get_local_search_store
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question


_CITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bмоскв\w*\b", re.I), "Москва"),
    (re.compile(r"\b(?:санкт[-\s]?петербург\w*|петербург\w*|спб)\b", re.I), "Санкт-Петербург"),
    (re.compile(r"\bказан\w*\b", re.I), "Казань"),
    (re.compile(r"\bекатеринбург\w*\b", re.I), "Екатеринбург"),
    (re.compile(r"\bновосибирск\w*\b", re.I), "Новосибирск"),
    (re.compile(r"\bсамар\w*\b", re.I), "Самара"),
    (re.compile(r"\bчелябинск\w*\b", re.I), "Челябинск"),
    (re.compile(r"\bкрасноярск\w*\b", re.I), "Красноярск"),
    (re.compile(r"\bтюмен\w*\b", re.I), "Тюмень"),
    (re.compile(r"\bуф\w*\b", re.I), "Уфа"),
    (re.compile(r"\bперм\w*\b", re.I), "Пермь"),
    (re.compile(r"\bсочи\b", re.I), "Сочи"),
    (re.compile(r"\bкалининград\w*\b", re.I), "Калининград"),
    (re.compile(r"\bворонеж\w*\b", re.I), "Воронеж"),
    (re.compile(r"\bкраснодар\w*\b", re.I), "Краснодар"),
    (re.compile(r"\bомск\w*\b", re.I), "Омск"),
    (re.compile(r"\bнижн\w*\s+новгород\w*\b", re.I), "Нижний Новгород"),
    (re.compile(r"\bростов\w*(?:-на-дону)?\b", re.I), "Ростов-на-Дону"),
)

_CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"автосервис|авторемонт|ремонт\w*\s+(?:авто|машин)|\bсто\b", re.I), "car_repair", "автосервис"),
    (re.compile(r"шиномонтаж|шины|покрышк", re.I), "tyres", "шиномонтаж"),
    (re.compile(r"автомойк", re.I), "car_wash", "автомойка"),
    (re.compile(r"детейлинг", re.I), "detailing", "детейлинг"),
    (re.compile(r"слухопротез|слухов\w*\s+аппарат|сурдолог|аудиолог", re.I), "hearing_aids", "слухопротезирование"),
    (re.compile(r"стоматолог", re.I), "dentist", "стоматология"),
    (re.compile(r"клиник|медицин\w*\s+центр|диагност\w*\s+центр", re.I), "clinic", "клиника"),
    (re.compile(r"аптек", re.I), "pharmacy", "аптека"),
    (re.compile(r"ветеринар|ветклиник", re.I), "veterinary", "ветеринария"),
    (re.compile(r"юрист|адвокат|юридичес", re.I), "lawyer", "юридические услуги"),
    (re.compile(r"нотариус", re.I), "notary", "нотариус"),
    (re.compile(r"риелтор|риэлтор|недвижим", re.I), "estate_agent", "недвижимость"),
    (re.compile(r"страхов", re.I), "insurance", "страхование"),
    (re.compile(r"банк\w*", re.I), "bank", "банк"),
    (re.compile(r"ресторан", re.I), "restaurant", "ресторан"),
    (re.compile(r"кафе|кофейн", re.I), "cafe", "кафе"),
    (re.compile(r"бар\b|паб\b", re.I), "bar", "бар"),
    (re.compile(r"пицц", re.I), "pizza", "пиццерия"),
    (re.compile(r"суши", re.I), "sushi", "суши"),
    (re.compile(r"фитнес|спортзал|тренаж", re.I), "fitness_centre", "фитнес"),
    (re.compile(r"йога", re.I), "yoga", "йога"),
    (re.compile(r"салон\w*\s+красот|косметолог|маникюр|педикюр", re.I), "beauty", "салон красоты"),
    (re.compile(r"парикмах|барбершоп", re.I), "hairdresser", "парикмахерская"),
    (re.compile(r"массаж", re.I), "massage", "массаж"),
    (re.compile(r"химчист", re.I), "dry_cleaning", "химчистка"),
    (re.compile(r"прачеч", re.I), "laundry", "прачечная"),
    (re.compile(r"ателье|портн", re.I), "tailor", "ателье"),
    (re.compile(r"типограф|копицентр|полиграф", re.I), "printing", "типография"),
    (re.compile(r"цветоч|цветы", re.I), "florist", "цветы"),
    (re.compile(r"мебел", re.I), "furniture", "мебель"),
    (re.compile(r"пекар", re.I), "bakery", "пекарня"),
    (re.compile(r"отел|гостиниц", re.I), "hotel", "отель"),
    (re.compile(r"автошкол", re.I), "driving_school", "автошкола"),
    (re.compile(r"языков\w*\s+школ|английск\w*\s+школ", re.I), "language_school", "языковая школа"),
    (re.compile(r"строител\w*\s+(?:компани|фирм)", re.I), "construction", "строительная компания"),
    (re.compile(r"электрик", re.I), "electrician", "электрик"),
    (re.compile(r"сантехник", re.I), "plumber", "сантехник"),
    (re.compile(r"ремонт\w*\s+(?:телефон|смартфон)", re.I), "mobile_phone_repair", "ремонт телефонов"),
    (re.compile(r"ремонт\w*\s+(?:компьютер|ноутбук)", re.I), "computer_repair", "ремонт компьютеров"),
)

_NOISE = re.compile(
    r"\b(?:лучши\w*|топ|рейтинг\w*|найди\w*|найти|подбер\w*|посовет\w*|порекоменду\w*|"
    r"покажи\w*|выбрать|отзыв\w*|недорог\w*|хорош\w*)\b",
    re.I,
)
_QUALITY_RE = re.compile(r"\b(?:лучши\w*|топ|рейтинг\w*|хорош\w*|отзыв\w*|рекоменду\w*|посовет\w*)\b", re.I)


def city_from_question(question: str) -> str:
    text = normalized_question(question)
    for pattern, city in _CITY_PATTERNS:
        if pattern.search(text):
            return city
    return ""


def category_from_question(question: str) -> tuple[str, str]:
    text = normalized_question(question)
    for pattern, category, label in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category, label
    return "", ""


def search_text_from_question(question: str) -> str:
    text = _NOISE.sub(" ", " ".join(str(question or "").split()))
    for pattern, _city in _CITY_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\b(?:в|во|на|около|рядом|поблизости)\b", " ", text, flags=re.I)
    return " ".join(text.split()).strip()


def _city_match(hit: BusinessHit, city: str) -> bool:
    if not city:
        return True
    return hit.city.casefold().replace("ё", "е") == city.casefold().replace("ё", "е")


def _provider(value: str) -> str:
    return value if value in {"osm", "yandex_maps", "2gis", "zoon", "yell"} else "local_index"


def _card(source_url: str, website: str, business_id: int, lat: float | None, lon: float | None) -> str:
    card = source_url or website
    if not card and lat is not None and lon is not None:
        card = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}"
    return card or f"local://business/{business_id}"


def _hit_to_place(hit: BusinessHit, *, rating: float | None = None, reviews: int | None = None) -> MapPlace:
    card = _card(hit.source_url, hit.website, hit.id, hit.lat, hit.lon)
    return MapPlace(
        provider=_provider(hit.source),
        name=hit.name,
        card_url=card,
        address=hit.address,
        phone=hit.phone,
        website=hit.website,
        rating=rating,
        reviews=reviews,
        source_url=hit.source_url or card,
    )


def _ranked_quality_places(
    store: LocalSearchStore,
    *,
    city: str,
    category: str,
    limit: int,
) -> list[MapPlace]:
    """Rank one local vertical by rating confidence, fully on local SQLite data."""
    ensure_business_quality_schema(store)
    connection = sqlite3.connect(str(store.path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id,name,address,phone,website,source,source_url,lat,lon,
                   confidence,rating,review_count,source_updated_at
            FROM businesses
            WHERE city=? AND category=?
            ORDER BY
                CASE WHEN rating IS NULL THEN 1 ELSE 0 END ASC,
                rating DESC,
                COALESCE(review_count,0) DESC,
                confidence DESC
            LIMIT 1000
            """,
            (city, category),
        ).fetchall()
    finally:
        connection.close()

    def rank(row: sqlite3.Row) -> tuple[float, int, float, int, str]:
        rating = float(row["rating"]) if row["rating"] is not None else None
        reviews = int(row["review_count"]) if row["review_count"] is not None else None
        completeness = sum(bool(row[key]) for key in ("address", "phone", "website"))
        return (
            quality_score(rating, reviews),
            int(reviews or 0),
            float(row["confidence"] or 0.0),
            completeness,
            str(row["name"] or "").casefold(),
        )

    ranked = sorted(rows, key=rank, reverse=True)
    result: list[MapPlace] = []
    seen: set[str] = set()
    for row in ranked:
        name = " ".join(str(row["name"] or "").split()).strip()
        if len(name) < 2:
            continue
        dedupe = name.casefold()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rating = float(row["rating"]) if row["rating"] is not None else None
        reviews = int(row["review_count"]) if row["review_count"] is not None else None
        lat = float(row["lat"]) if row["lat"] is not None else None
        lon = float(row["lon"]) if row["lon"] is not None else None
        source_url = str(row["source_url"] or "")
        website = str(row["website"] or "")
        card = _card(source_url, website, int(row["id"]), lat, lon)
        result.append(
            MapPlace(
                provider=_provider(str(row["source"] or "")),
                name=name,
                card_url=card,
                address=str(row["address"] or ""),
                phone=str(row["phone"] or ""),
                website=website,
                rating=rating,
                reviews=reviews,
                source_url=source_url or card,
            )
        )
        if len(result) >= max(1, limit):
            break
    return result


def discover_local_businesses(question: str, *, limit: int = 12) -> list[MapPlace]:
    store = get_local_search_store()
    city = city_from_question(question)
    category, _category_label = category_from_question(question)
    query = search_text_from_question(question)
    quality_requested = bool(_QUALITY_RE.search(normalized_question(question)))

    if quality_requested and city and category:
        ranked = _ranked_quality_places(store, city=city, category=category, limit=limit)
        if ranked:
            return ranked

    fetch_limit = max(50, limit * 8)
    if category:
        candidates = store.search_businesses("", category=category, limit=fetch_limit)
    else:
        candidates = store.search_businesses(query, limit=fetch_limit)
    hits = [hit for hit in candidates if _city_match(hit, city)]

    if len(hits) < min(5, limit) and query:
        extra = store.search_businesses(query, limit=fetch_limit)
        seen = {item.id for item in hits}
        hits.extend(item for item in extra if item.id not in seen and _city_match(item, city))

    quality = load_business_quality((hit.id for hit in hits), store=store)
    hits.sort(key=lambda item: (-item.confidence, item.score, item.name.casefold()))
    return [
        _hit_to_place(
            hit,
            rating=quality.get(hit.id).rating if hit.id in quality else None,
            reviews=quality.get(hit.id).reviews if hit.id in quality else None,
        )
        for hit in hits[:limit]
    ]


def persist_places(question: str, places: list[MapPlace]) -> int:
    if not places:
        return 0
    store = get_local_search_store()
    city = city_from_question(question)
    category, _label = category_from_question(question)
    now = datetime.now(timezone.utc)
    stale_after = (now + timedelta(days=14)).isoformat()
    written = 0
    for place in places:
        if not place.name or str(place.card_url or "").startswith("local://"):
            continue
        source = str(place.provider or "external")
        source_id = ""
        match = re.search(r"/(?:node|way|relation|firm|profile)/(\d+)", str(place.card_url or ""))
        if match:
            source_id = match.group(1)
        try:
            business_id = store.upsert_business(
                {
                    "name": place.name,
                    "category": category,
                    "city": city,
                    "address": place.address,
                    "phone": place.phone,
                    "website": place.website,
                    "source": source,
                    "source_url": place.card_url or place.source_url,
                    "source_id": source_id,
                    "confidence": 0.78 if source in {"osm", "yandex_maps", "2gis"} else 0.62,
                    "updated_at": now.isoformat(),
                    "stale_after": stale_after,
                }
            )
            if place.rating is not None or place.reviews is not None:
                from app.services.business_quality import save_business_quality

                save_business_quality(
                    business_id,
                    rating=place.rating,
                    reviews=place.reviews,
                    source_updated_at=now.isoformat(),
                    store=store,
                )
            written += 1
        except (ValueError, OSError, sqlite3.Error):
            continue
    return written


def local_index_stats() -> dict:
    return get_local_search_store().stats()
