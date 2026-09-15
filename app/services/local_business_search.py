from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from app.services.discovery import DiscoveryError, SearchHit, canonical_result_url
from app.services.response_strategy import normalized_question


_LOCAL_RE = re.compile(
    r"(?:\b(?:лучши\w*|топ|рейтинг|найди|подбери|посовет\w*)\b.{0,120}"
    r"\b(?:компани\w*|центр\w*|клиник\w*|сервис\w*|магазин\w*|салон\w*|"
    r"стоматолог\w*|слухопротезирован\w*|аптек\w*|ресторан\w*|кафе|отел\w*)\b|"
    r"\b(?:компани\w*|центр\w*|клиник\w*|сервис\w*|магазин\w*|салон\w*|"
    r"стоматолог\w*|слухопротезирован\w*|аптек\w*|ресторан\w*|кафе|отел\w*)\b.{0,100}"
    r"\b(?:в|рядом|поблизости)\s+[а-яёa-z-]+)",
    re.I,
)
_GENERIC_TITLE = re.compile(
    r"\b(?:яндекс\s*карты|yandex\s*maps|google\s*maps|2гис|2gis|карты|maps|"
    r"официальный\s*сайт|отзывы|адрес|телефон|москва|moscow)\b",
    re.I,
)
_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
_SPACE = re.compile(r"\s+")
_STOP = {
    "лучшие", "лучший", "лучших", "топ", "рейтинг", "найди", "подбери", "посоветуй",
    "компания", "компании", "компаний", "центр", "центры", "центров", "клиника", "клиники",
    "в", "во", "на", "рядом", "поблизости", "москва", "москве", "москвы",
}


@dataclass(frozen=True)
class LocalBusinessResult:
    text: str
    sources: list[dict]
    searched: int


def is_local_business_question(question: str) -> bool:
    return bool(_LOCAL_RE.search(normalized_question(question)))


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _kind(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = (parsed.path or "").casefold()
    if host.endswith("yandex.ru") and path.startswith("/maps"):
        return "yandex_maps"
    if host.endswith("2gis.ru"):
        return "2gis"
    if (host.endswith("google.com") and path.startswith("/maps")) or host == "maps.google.com":
        return "google_maps"
    return "web"


def _clean_title(value: str) -> str:
    text = _SPACE.sub(" ", str(value or "")).strip(" -–—|·:,.\t\n")
    for sep in (" — ", " | ", " - ", " · "):
        if sep in text:
            left, right = text.split(sep, 1)
            if len(left.strip()) >= 3 and _GENERIC_TITLE.search(right):
                text = left.strip()
                break
    text = _GENERIC_TITLE.sub(" ", text)
    text = _SPACE.sub(" ", text).strip(" -–—|·:,.\t\n")
    return text[:120]


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
    # For map cards a single category/location concept may be enough because the
    # URL itself is already constrained to a map provider. Ordinary web rows
    # need stronger lexical agreement.
    return matches >= (1 if _kind(hit.url) != "web" else min(2, len(terms)))


def _entity_key(title: str) -> str:
    value = _clean_title(title).casefold()
    value = re.sub(r"[^a-zа-яё0-9]+", " ", value)
    tokens = [t for t in value.split() if len(t) >= 2]
    return " ".join(tokens[:8])


def _map_search_links(name: str, question: str) -> dict[str, str]:
    query = quote((name or question).strip(), safe="")
    return {
        "yandex_search": f"https://yandex.ru/maps/?text={query}",
        "google_search": f"https://www.google.com/maps/search/?api=1&query={query}",
        "twogis_search": f"https://2gis.ru/moscow/search/{query}",
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
    safe = str(url or "").replace(")", "%29")
    return f"[{label}]({safe})"


async def _search(discovery, query: str, *, timeout: float = 4.5) -> list[SearchHit]:
    try:
        return await asyncio.wait_for(
            discovery.search(query, count=8, country="RU", language="ru"),
            timeout=timeout,
        )
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        return []


async def resolve_local_business(question: str, discovery) -> LocalBusinessResult | None:
    if not is_local_business_question(question):
        return None

    queries = (
        question,
        f'site:yandex.ru/maps/org/ {question}',
        f'site:2gis.ru {question}',
        f'site:google.com/maps {question}',
    )
    batches = await asyncio.gather(*(_search(discovery, q) for q in queries))

    hits: list[SearchHit] = []
    seen_urls: set[str] = set()
    for batch in batches:
        for hit in batch:
            if not _relevant(hit, question):
                continue
            url = canonical_result_url(hit.url)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            hits.append(hit)

    if not hits:
        return LocalBusinessResult(
            text=(
                "Не удалось получить подтверждённые карточки компаний из поиска и карт. "
                "Я не буду придумывать названия, адреса или рейтинги."
            ),
            sources=[],
            searched=0,
        )

    priority = {"yandex_maps": 0, "2gis": 1, "google_maps": 2, "web": 3}
    hits.sort(key=lambda h: (priority.get(_kind(h.url), 9), int(getattr(h, "rank", 99))))

    entities: dict[str, dict] = {}
    source_rows: list[dict] = []
    for hit in hits:
        source_rows.append(_source_row(hit))
        name = _clean_title(hit.title)
        if not name or len(name) < 3:
            continue
        key = _entity_key(name)
        if not key:
            continue
        row = entities.setdefault(key, {
            "name": name,
            "snippet": "",
            "web": "",
            "yandex_maps": "",
            "2gis": "",
            "google_maps": "",
        })
        kind = _kind(hit.url)
        if kind in {"yandex_maps", "2gis", "google_maps"}:
            row[kind] = row[kind] or hit.url
        else:
            row["web"] = row["web"] or hit.url
        if not row["snippet"] and hit.snippet:
            row["snippet"] = _SPACE.sub(" ", str(hit.snippet)).strip()[:220]
        if len(entities) >= 8 and sum(1 for r in entities.values() if r["yandex_maps"] or r["2gis"] or r["google_maps"]) >= 5:
            break

    rows = list(entities.values())
    if not rows:
        return LocalBusinessResult(
            text="Поиск ответил, но подтверждённых карточек организаций извлечь не удалось.",
            sources=source_rows[:10],
            searched=len(source_rows),
        )

    def entity_score(row: dict) -> tuple[int, int]:
        maps = sum(bool(row[k]) for k in ("yandex_maps", "2gis", "google_maps"))
        return (maps, 1 if row["web"] else 0)

    rows.sort(key=entity_score, reverse=True)
    rows = rows[:5]

    out = [
        "Нашёл подтверждённые варианты по поисковой выдаче и картам. "
        "Конкретную карточку показываю только когда она реально найдена; иначе даю активный поиск по названию на карте."
    ]
    for index, row in enumerate(rows, start=1):
        name = row["name"]
        fallback = _map_search_links(name, question)
        out.append(f"\n{index}. **{name}**")
        if row["snippet"]:
            out.append(row["snippet"])
        if row["web"]:
            out.append("Сайт/источник: " + _md("открыть", row["web"]))

        yandex_url = row["yandex_maps"] or fallback["yandex_search"]
        google_url = row["google_maps"] or fallback["google_search"]
        twogis_url = row["2gis"] or fallback["twogis_search"]
        out.append("Яндекс Карты: " + _md("карточка" if row["yandex_maps"] else "поиск на карте", yandex_url))
        out.append("Google Maps: " + _md("карточка" if row["google_maps"] else "поиск на карте", google_url))
        out.append("2ГИС: " + _md("карточка" if row["2gis"] else "поиск на карте", twogis_url))

    out.append("\nРейтинг и число отзывов не указываю, если они не подтверждены найденной карточкой или сниппетом.")
    return LocalBusinessResult(text="\n".join(out), sources=source_rows[:10], searched=len(source_rows))
