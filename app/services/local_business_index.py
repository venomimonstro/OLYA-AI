from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.services.local_search_store import BusinessHit, get_local_search_store
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


def _hit_to_place(hit: BusinessHit) -> MapPlace:
    source = hit.source if hit.source in {"osm", "yandex_maps", "2gis", "zoon", "yell"} else "local_index"
    card = hit.source_url or hit.website
    if not card and hit.lat is not None and hit.lon is not None:
        card = f"https://www.openstreetmap.org/?mlat={hit.lat:.6f}&mlon={hit.lon:.6f}#map=17/{hit.lat:.6f}/{hit.lon:.6f}"
    return MapPlace(
        provider=source,
        name=hit.name,
        card_url=card or f"local://business/{hit.id}",
        address=hit.address,
        phone=hit.phone,
        website=hit.website,
        source_url=hit.source_url or card,
    )


def discover_local_businesses(question: str, *, limit: int = 12) -> list[MapPlace]:
    store = get_local_search_store()
    city = city_from_question(question)
    category, category_label = category_from_question(question)
    query = search_text_from_question(question)

    # Category is the strongest signal for known verticals; FTS provides fuzzy
    # name/description matching for unknown wording. Try both paths so queries
    # such as "лучшие автосервисы Москва" do not depend on exact morphology.
    hits = store.search_businesses(query, city=city, category=category, limit=limit)
    if len(hits) < min(5, limit) and category:
        extra = store.search_businesses(category_label or category, city=city, category=category, limit=limit)
        seen = {item.id for item in hits}
        hits.extend(item for item in extra if item.id not in seen)
    if len(hits) < min(5, limit) and city:
        extra = store.search_businesses(query or category_label, city=city, limit=limit)
        seen = {item.id for item in hits}
        hits.extend(item for item in extra if item.id not in seen)
    return [_hit_to_place(hit) for hit in hits[:limit]]


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
            store.upsert_business(
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
            written += 1
        except (ValueError, OSError):
            continue
    return written


def local_index_stats() -> dict:
    return get_local_search_store().stats()
