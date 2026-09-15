from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, urlsplit

from app.services.business_local_index import load_places, store_places
from app.services.business_quality import quality_score
from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.indexed_business_discovery import discover_indexed_businesses
from app.services.local_business_catalog import search_local_catalog
from app.services.osm_business_discovery import discover_osm_businesses
from app.services.public_maps_discovery import MapPlace
from app.services.response_strategy import normalized_question
from app.services.russian_maps_discovery import RussianMapsDiscovery
from app.services.yandex_medicine_discovery import discover_yandex_medicine, is_yandex_medicine_url
from app.services.yell_discovery import discover_yell
from app.services.zoon_discovery import discover_zoon

_SELECTION_RE = re.compile(r"\b(?:лучши\w*|топ|рейтинг\w*|найди\w*|найти|подбер\w*|посовет\w*|порекоменду\w*|рекоменду\w*|покажи\w*|выбер\w*|выбрать)\b", re.I)
_LOCATION_RE = re.compile(r"\b(?:в|во|рядом|поблизости|около)\s+[а-яёa-z][а-яёa-z-]{2,}(?:\s+[а-яёa-z][а-яёa-z-]{2,}){0,2}\b", re.I)
_CITY_MENTION_RE = re.compile(r"\b(?:москв\w*|санкт[-\s]?петербург\w*|петербург\w*|спб|казан\w*|екатеринбург\w*|новосибирск\w*|самар\w*|челябинск\w*|красноярск\w*|тюмен\w*|уф\w*|перм\w*|сочи|калининград\w*|воронеж\w*|краснодар\w*|омск\w*|нижн\w*\s+новгород\w*|ростов\w*(?:-на-дону)?)\b", re.I)
_LOCAL_ACTION_RE = re.compile(r"\b(?:отзыв\w*|цен\w*|стоимост\w*|адрес\w*|телефон\w*|контакт\w*|рядом|недорог\w*|где\s+(?:найти|купить|заказать|обратиться)|куда\s+обратиться)\b", re.I)
_BUSINESS_GENERIC_RE = re.compile(r"\b(?:компани\w*|фирм\w*|сервис\w*|автосервис\w*|центр\w*|клиник\w*|магазин\w*|салон\w*|студи\w*|агентств\w*|школ\w*|курс\w*|ресторан\w*|кафе|бар\w*|отел\w*|гостиниц\w*|юрист\w*|адвокат\w*|нотариус\w*|фитнес\w*|спортзал\w*|ремонт\w*|мастер\w*|доставк\w*|пекар\w*|цветоч\w*|мебел\w*|стоматолог\w*|аптек\w*|лаборатор\w*|ветеринар\w*|страхов\w*|банк\w*|риелтор\w*|риэлтор\w*|строител\w*|типограф\w*|ателье\w*|химчист\w*|шиномонтаж\w*|автомойк\w*|детейлинг\w*|слухопротезирован\w*)\b", re.I)
_NON_BUSINESS_LOCAL_RE = re.compile(r"\b(?:погод\w*|район\w*|улиц\w*|проспект\w*|метро\b|маршрут\w*|населени\w*|мэр\w*|губернатор\w*|новост\w*|истори\w*|экономик\w*|достопримечательност\w*|что\s+посмотреть|куда\s+сходить|прогул\w*)\b", re.I)
_MEDICAL_RE = re.compile(r"\b(?:медицин\w*|клиник\w*|стоматолог\w*|врач\w*|доктор\w*|сурдолог\w*|слухопротезирован\w*|слухов\w+\s+аппарат\w*|лаборатор\w*|диагност\w*)\b", re.I)
_GENERIC_TITLE = re.compile(r"\b(?:яндекс\s*карты|yandex\s*maps|яндекс\s*медицина|2гис|2gis|zoon|зун|yell|openstreetmap|osm|карты|maps|официальный\s*сайт|отзывы|адрес|телефон|москва|moscow)\b", re.I)
_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
_SPACE = re.compile(r"\s+")
_STOP = {"лучшие", "лучший", "лучших", "топ", "рейтинг", "найди", "найти", "подбери", "посоветуй", "порекомендуй", "покажи", "компания", "компании", "компаний", "центр", "центры", "центров", "клиника", "клиники", "в", "во", "на", "рядом", "поблизости", "около", "москва", "москве", "москвы"}
_CITY_SLUGS = {
    "москва": "moscow", "москве": "moscow", "москвы": "moscow",
    "санкт-петербург": "spb", "санкт-петербурге": "spb", "петербург": "spb", "спб": "spb",
    "казань": "kazan", "казани": "kazan", "екатеринбург": "ekaterinburg", "екатеринбурге": "ekaterinburg",
    "новосибирск": "novosibirsk", "новосибирске": "novosibirsk", "самара": "samara", "самаре": "samara",
    "челябинск": "chelyabinsk", "челябинске": "chelyabinsk", "красноярск": "krasnoyarsk", "красноярске": "krasnoyarsk",
    "тюмень": "tyumen", "тюмени": "tyumen", "уфа": "ufa", "уфе": "ufa", "пермь": "perm", "перми": "perm", "сочи": "sochi",
    "калининград": "kaliningrad", "калининграде": "kaliningrad", "воронеж": "voronezh", "воронеже": "voronezh",
    "краснодар": "krasnodar", "краснодаре": "krasnodar", "омск": "omsk", "омске": "omsk",
    "нижний новгород": "nnovgorod", "нижнем новгороде": "nnovgorod", "ростов-на-дону": "rostov", "ростове-на-дону": "rostov",
}
_RU_MAPS = RussianMapsDiscovery(timeout_seconds=3.8)


@dataclass(frozen=True)
class LocalBusinessResult:
    text: str
    sources: list[dict]
    searched: int


def is_local_business_question(question: str) -> bool:
    text = normalized_question(question)
    if not text or _NON_BUSINESS_LOCAL_RE.search(text):
        return False
    if not (_CITY_MENTION_RE.search(text) or _LOCATION_RE.search(text)):
        return False
    if _SELECTION_RE.search(text):
        return True
    return bool(_BUSINESS_GENERIC_RE.search(text) and (_CITY_MENTION_RE.search(text) or _LOCAL_ACTION_RE.search(text)))


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _kind(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = (parsed.path or "").casefold()
    if host.endswith(("yandex.ru", "yandex.com")) and (path.startswith("/maps") or path.startswith("/profile/") or path.startswith("/medicine/clinic/")):
        return "yandex_maps"
    if host.endswith("2gis.ru"):
        return "2gis"
    if host == "zoon.ru":
        return "zoon"
    if host == "yell.ru":
        return "yell"
    if host.endswith("openstreetmap.org"):
        return "osm"
    return "web"


def _clean_title(value: str) -> str:
    text = _SPACE.sub(" ", str(value or "")).strip(" -–—|·:,.\t\n")
    for sep in (" — ", " | ", " - ", " · "):
        if sep in text:
            left, right = text.split(sep, 1)
            if len(left.strip()) >= 3 and _GENERIC_TITLE.search(right):
                text = left.strip()
                break
    return _SPACE.sub(" ", _GENERIC_TITLE.sub(" ", text)).strip(" -–—|·:,.\t\n")[:140]


def _stem(value: str) -> str:
    value = value.casefold()
    return value[:7] if len(value) >= 7 else value


def _query_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in _WORD.findall(normalized_question(question)):
        if len(token) < 4 or token in _STOP or token.isdigit():
            continue
        stem = _stem(token)
        if stem not in terms:
            terms.append(stem)
    return tuple(terms[:6])


def _relevant(hit: SearchHit, question: str) -> bool:
    terms = _query_terms(question)
    if not terms:
        return True
    haystack = " ".join((str(hit.title or ""), str(hit.snippet or ""), str(hit.url or ""))).casefold()
    matches = sum(1 for term in terms if term in haystack)
    return matches >= (1 if _kind(hit.url) != "web" else min(2, len(terms)))


def _entity_key(title: str) -> str:
    value = re.sub(r"[^a-zа-яё0-9]+", " ", _clean_title(title).casefold())
    return " ".join(token for token in value.split() if len(token) >= 2)[:180]


def _city_slug(question: str) -> str:
    text = normalized_question(question)
    for alias, slug in _CITY_SLUGS.items():
        if alias in text:
            return slug
    return "moscow"


def _map_search_links(name: str, *, city_slug: str, address: str = "") -> dict[str, str]:
    query = " ".join(part for part in (name, address) if part).strip()
    return {
        "yandex": f"https://yandex.ru/maps/?text={quote_plus(query)}",
        "2gis": f"https://2gis.ru/{city_slug}/search/{quote(query, safe='')}",
    }


def _source_row(hit: SearchHit) -> dict:
    return {
        "title": _clean_title(hit.title) or hit.title or _host(hit.url),
        "url": hit.url,
        "domain": _host(hit.url),
        "provider": hit.provider,
        "snippet": str(hit.snippet or "")[:300],
    }


def _md(label: str, url: str) -> str:
    return f"[{label}]({str(url or '').replace(')', '%29')})"


async def _search(discovery, query: str, *, timeout: float = 3.4) -> list[SearchHit]:
    try:
        return await asyncio.wait_for(discovery.search(query, count=10, country="RU", language="ru"), timeout=timeout)
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        return []


async def _empty_places() -> list[MapPlace]:
    return []


def _best_review_signal(row: dict) -> tuple[float | None, int | None, str]:
    candidates: list[tuple[float, int, str]] = []
    for provider, rating in row.get("ratings", {}).items():
        if not isinstance(rating, (int, float)):
            continue
        reviews = row.get("reviews", {}).get(provider)
        count = int(reviews) if isinstance(reviews, int) else 0
        candidates.append((float(rating), count, provider))
    if not candidates:
        return None, None, ""
    rating, reviews, provider = max(candidates, key=lambda item: (quality_score(item[0], item[1]), item[1], item[0]))
    return rating, reviews, provider


def _entity_score(row: dict) -> float:
    map_evidence = sum(bool(row.get(key)) for key in ("yandex_maps", "2gis"))
    directory_evidence = sum(bool(row.get(key)) for key in ("zoon", "yell"))
    osm_evidence = 1 if row.get("osm") else 0
    independent = map_evidence + directory_evidence + osm_evidence + (1 if row.get("web") else 0)
    rating, reviews, _provider = _best_review_signal(row)
    quality = quality_score(rating, reviews)
    completeness = sum(bool(row.get(key)) for key in ("address", "phone", "website", "web")) / 4.0
    rating_component = (quality * 4.0) if rating is not None else 0.0
    return rating_component + map_evidence * 1.2 + directory_evidence * 0.7 + osm_evidence * 0.4 + min(independent, 5) * 0.15 + completeness * 0.8


def _empty_entity(name: str) -> dict:
    return {
        "name": name,
        "address": "",
        "phone": "",
        "website": "",
        "web": "",
        "yandex_maps": "",
        "2gis": "",
        "zoon": "",
        "yell": "",
        "osm": "",
        "ratings": {},
        "reviews": {},
    }


def _merge_place(entities: dict[str, dict], place: MapPlace) -> None:
    name = _clean_title(place.name)
    key = _entity_key(name)
    if not key:
        return
    row = entities.setdefault(key, _empty_entity(name))
    if place.address and not row["address"]:
        row["address"] = place.address
    if place.phone and not row["phone"]:
        row["phone"] = place.phone
    if place.website and not row["website"]:
        row["website"] = place.website
    if place.provider in {"yandex_maps", "2gis", "zoon", "yell", "osm"}:
        row[place.provider] = row[place.provider] or place.card_url
    if place.rating is not None:
        row["ratings"][place.provider] = place.rating
    if place.reviews is not None:
        row["reviews"][place.provider] = place.reviews


def _places(value) -> list[MapPlace]:
    return value if isinstance(value, list) else []


def _hits(value) -> list[SearchHit]:
    return value if isinstance(value, list) else []


def _why_in_list(row: dict) -> str:
    rating, reviews, _provider = _best_review_signal(row)
    reasons: list[str] = []
    if rating is not None:
        if rating >= 4.8 and (reviews or 0) >= 100:
            reasons.append("очень высокий рейтинг при большой выборке отзывов")
        elif rating >= 4.7:
            reasons.append("высокий пользовательский рейтинг")
        elif (reviews or 0) >= 200:
            reasons.append("заметная база отзывов")
    if (reviews or 0) >= 500 and "очень высокий рейтинг при большой выборке отзывов" not in reasons:
        reasons.append("много отзывов, поэтому оценка выглядит устойчивее")
    if row.get("address") and row.get("phone") and (row.get("website") or row.get("web")):
        reasons.append("карточка хорошо заполнена: есть адрес, телефон и сайт")
    elif row.get("address") and row.get("phone"):
        reasons.append("есть подтверждённые адрес и телефон")
    return "; ".join(reasons[:2]) or "есть подтверждённая карточка организации и контактные данные"


def _provider_label(provider: str) -> str:
    return {
        "2gis": "2ГИС",
        "yandex_maps": "Яндекс Карты",
        "zoon": "Zoon",
        "yell": "Yell",
        "osm": "OpenStreetMap",
    }.get(provider, provider)


def _render(question: str, places: list[MapPlace], source_rows: list[dict], *, cache_only: bool) -> LocalBusinessResult:
    entities: dict[str, dict] = {}
    for place in places:
        _merge_place(entities, place)
    rows = [
        row
        for row in entities.values()
        if any(row.get(key) for key in ("yandex_maps", "2gis", "zoon", "yell", "osm", "web"))
    ]
    rows.sort(key=_entity_score, reverse=True)
    rows = rows[:7]
    if not rows:
        return LocalBusinessResult(
            text="По этому запросу пока недостаточно подтверждённых данных. Я не буду заполнять пробелы вымышленными компаниями.",
            sources=[],
            searched=0,
        )

    top_rating, top_reviews, top_provider = _best_review_signal(rows[0])
    rated_count = sum(1 for row in rows if _best_review_signal(row)[0] is not None)
    out: list[str] = []
    if top_rating is not None:
        lead = f"Если выбирать по сочетанию рейтинга, количества отзывов и полноты карточки, я бы сначала посмотрел **{rows[0]['name']}**."
        if top_reviews:
            lead += f" У него {top_rating:g}/5 на основе {top_reviews} отзывов/оценок в {_provider_label(top_provider)}."
        else:
            lead += f" Его опубликованный рейтинг — {top_rating:g}/5 в {_provider_label(top_provider)}."
        out.append(lead)
        out.append(f"Ниже — {len(rows)} наиболее сильных вариантов из подтверждённых данных. Рейтинг есть у {rated_count} из них; отсутствие рейтинга я не заменяю догадками.")
    else:
        out.append(
            f"Нашёл {len(rows)} подтверждённых вариантов. У этих карточек сейчас нет надёжно сохранённого рейтинга, поэтому я не называю порядок объективным «топом»: сортировка опирается на полноту и подтверждённость данных."
        )

    for index, row in enumerate(rows, 1):
        rating, reviews, provider = _best_review_signal(row)
        headline = f"{index}. **{row['name']}**"
        if rating is not None:
            headline += f" — {rating:g}/5"
            if reviews is not None:
                headline += f", {reviews} отзывов/оценок"
        out.append(f"\n{headline}")
        out.append(f"Почему в подборке: {_why_in_list(row)}.")
        if row.get("address"):
            out.append(f"Адрес: {row['address']}")
        contact_parts: list[str] = []
        if row.get("phone"):
            contact_parts.append(row["phone"])
        website = row.get("website") or row.get("web")
        if website:
            contact_parts.append(_md("сайт", website))
        if contact_parts:
            out.append("Контакты: " + " · ".join(contact_parts))
        source_links: list[str] = []
        for label, key in (("2ГИС", "2gis"), ("Яндекс Карты", "yandex_maps"), ("Zoon", "zoon"), ("Yell", "yell"), ("OSM", "osm")):
            if row.get(key):
                source_links.append(_md(label, row[key]))
        if source_links:
            out.append("Карточки: " + " · ".join(source_links))

    if len(rows) >= 2:
        out.append("\n**Что выбрать**")
        out.append(f"• **{rows[0]['name']}** — мой первый вариант по совокупности доступных сигналов.")
        most_reviewed = max(rows, key=lambda row: (_best_review_signal(row)[1] or 0, _best_review_signal(row)[0] or 0.0))
        if most_reviewed is not rows[0] and (_best_review_signal(most_reviewed)[1] or 0) > 0:
            reviews = _best_review_signal(most_reviewed)[1]
            out.append(f"• **{most_reviewed['name']}** — стоит сравнить отдельно: у карточки самая большая база отзывов в этой выборке ({reviews}).")
        out.append("• Перед визитом лучше уточнить стоимость нужной услуги и ближайшее свободное время: эти параметры меняются быстрее рейтингов и контактных данных.")

    return LocalBusinessResult(text="\n".join(out), sources=source_rows[:20], searched=len(source_rows))


async def resolve_local_business(question: str, discovery, *, allow_external: bool = True) -> LocalBusinessResult | None:
    if not is_local_business_question(question):
        return None

    snapshot_rows = search_local_catalog(question, limit=30)
    cached_rows = load_places(question, limit=30)
    if snapshot_rows:
        store_places(question, snapshot_rows)
        merged = [*snapshot_rows, *cached_rows]
        return _render(question, merged, [place.public_source() for place in merged], cache_only=True)

    if len(cached_rows) >= 5:
        return _render(question, cached_rows, [place.public_source() for place in cached_rows], cache_only=True)

    if not allow_external:
        return LocalBusinessResult(
            text="В локальном каталоге пока недостаточно подходящих организаций для этого запроса. Внешний поиск отключён, поэтому я не буду подставлять непроверенные варианты.",
            sources=[],
            searched=0,
        )

    city_slug = _city_slug(question)
    medical = bool(_MEDICAL_RE.search(normalized_question(question)))
    queries = (
        question,
        f"site:yandex.ru/maps/org/ {question}",
        f"site:2gis.ru/{city_slug}/firm/ {question}",
        f"site:yell.ru/{city_slug}/com/ {question}",
    )
    provider_tasks = (
        asyncio.create_task(_RU_MAPS.search(question)),
        asyncio.create_task(discover_indexed_businesses(question, discovery, limit=10)),
        asyncio.create_task(discover_osm_businesses(question, limit=12, timeout_seconds=5.5)),
        asyncio.create_task(discover_zoon(question, discovery, limit=8)),
        asyncio.create_task(discover_yell(question, limit=8)),
        asyncio.create_task(discover_yandex_medicine(question, discovery, limit=8) if medical else _empty_places()),
    )
    serp_tasks = tuple(asyncio.create_task(_search(discovery, query)) for query in queries)
    gathered = await asyncio.gather(*provider_tasks, *serp_tasks, return_exceptions=True)
    public_rows, indexed_rows, osm_rows, zoon_rows, yell_rows, medicine_rows = (_places(gathered[i]) for i in range(6))
    live_batches = [osm_rows, indexed_rows, medicine_rows, public_rows, zoon_rows, yell_rows]
    for batch in live_batches:
        if batch:
            store_places(question, batch)
    merged_places = [*cached_rows]
    for batch in live_batches:
        merged_places.extend(batch)
    source_rows = [place.public_source() for place in merged_places]
    seen_urls = {canonical_result_url(row["url"]) for row in source_rows if row.get("url")}
    entities: dict[str, dict] = {}
    for place in merged_places:
        _merge_place(entities, place)
    for batch in [_hits(value) for value in gathered[6:]]:
        for hit in batch:
            if not _relevant(hit, question):
                continue
            canonical = canonical_result_url(hit.url)
            if not canonical or canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            source_rows.append(_source_row(hit))
            name = _clean_title(hit.title)
            key = _entity_key(name)
            if not key:
                continue
            row = entities.setdefault(key, _empty_entity(name))
            kind = _kind(hit.url)
            if kind in {"yandex_maps", "2gis", "zoon", "yell", "osm"}:
                row[kind] = row[kind] or hit.url
            elif not row["web"]:
                row["web"] = hit.url
            if is_yandex_medicine_url(hit.url) and not row["yandex_maps"]:
                row["yandex_maps"] = hit.url
    if merged_places:
        return _render(question, merged_places, source_rows, cache_only=False)
    return LocalBusinessResult(
        text="По этой категории пока нет достаточной локальной базы, а внешние источники сейчас недоступны. Я не буду придумывать компании — лучше вернуть пустой результат, чем ложную рекомендацию.",
        sources=[],
        searched=0,
    )
