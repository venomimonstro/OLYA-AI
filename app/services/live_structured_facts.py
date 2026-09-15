from __future__ import annotations

import asyncio
import html
import math
import re
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.schemas.chat import ChatMessage
from app.services.structured_facts import is_currency_rate_question, resolve_structured_fact
from app.services.task_solver import TaskExecution, TaskSolvePlan

_CYR = re.compile(r"[А-Яа-яЁё]")
_RUBLE = re.compile(r"\b(?:rub|руб(?:л(?:ь|я|ей|ю|ем)|\.?|ля|лей)?|₽)\b", re.I)
_DOLLAR = re.compile(r"\b(?:usd|доллар(?:а|ов|у|ом|ы)?|\$)\b", re.I)
_PRICE = re.compile(r"\b(?:курс|цена|стоимость|сколько\s+стоит|поч[её]м|котиров\w*|price|rate|quote|сейчас|щас|сегодня|current|today)\b", re.I)
_WEATHER = re.compile(r"\b(?:погода|температура|прогноз(?:\s+погоды)?|осадки|дождь|снег|ветер|weather|forecast|temperature)\b", re.I)
_TIME = re.compile(
    r"(?:\bкоторый\s+час\b|\bсколько\s+времени\b|\bсколько\s+(?:сейчас\s+|щас\s+)?в\s+.+?\s+времени\b|"
    r"\bв\s+.+?\s+сколько\s+времени\b|\bтекущее\s+время\b|\bвремя\s+(?:сейчас\s+|щас\s+)?в\b|"
    r"\bwhat\s+time\b|\bcurrent\s+time\b|\btime\s+in\b)", re.I,
)
_KEY_RATE = re.compile(r"\b(?:ключевая\s+ставка|ставка\s+(?:цб|банка\s+россии)|key\s+rate)\b", re.I)
_TOMORROW = re.compile(r"\b(?:завтра|на\s+завтра|tomorrow)\b", re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_CBR_KEY_RATE_URL = "https://www.cbr.ru/hd_base/KeyRate/"

_CRYPTO: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    ("bitcoin", "BTC", "биткоин", re.compile(r"\b(?:bitcoin|биткоин|биткойн|btc)\b", re.I)),
    ("ethereum", "ETH", "Ethereum", re.compile(r"\b(?:ethereum|эфириум|эфир|eth)\b", re.I)),
    ("solana", "SOL", "Solana", re.compile(r"\b(?:solana|солан[аы]|sol)\b", re.I)),
    ("the-open-network", "TON", "Toncoin", re.compile(r"\b(?:toncoin|ton\s+coin|тонкоин|ton)\b", re.I)),
    ("ripple", "XRP", "XRP", re.compile(r"\b(?:xrp|ripple|риппл)\b", re.I)),
    ("dogecoin", "DOGE", "Dogecoin", re.compile(r"\b(?:dogecoin|догикоин|doge)\b", re.I)),
)

_CITY_ALIASES = {
    "москва": "Москва", "москве": "Москва", "москвы": "Москва",
    "санкт-петербург": "Санкт-Петербург", "санкт-петербурге": "Санкт-Петербург",
    "петербург": "Санкт-Петербург", "петербурге": "Санкт-Петербург", "спб": "Санкт-Петербург",
    "казань": "Казань", "казани": "Казань", "самара": "Самара", "самаре": "Самара",
    "екатеринбург": "Екатеринбург", "екатеринбурге": "Екатеринбург",
    "новосибирск": "Новосибирск", "новосибирске": "Новосибирск",
    "красноярск": "Красноярск", "красноярске": "Красноярск",
    "тюмень": "Тюмень", "тюмени": "Тюмень", "уфа": "Уфа", "уфе": "Уфа",
    "пермь": "Пермь", "перми": "Пермь", "сочи": "Сочи",
    "челябинск": "Челябинск", "челябинске": "Челябинск",
    "ижевск": "Ижевск", "ижевске": "Ижевск", "барнаул": "Барнаул", "барнауле": "Барнаул",
    "лондон": "London", "лондоне": "London", "токио": "Tokyo", "дубай": "Dubai", "дубае": "Dubai",
    "нью-йорк": "New York", "нью-йорке": "New York", "берлин": "Berlin", "берлине": "Berlin",
    "париж": "Paris", "париже": "Paris", "амстердам": "Amsterdam", "амстердаме": "Amsterdam",
}

_WEATHER_CODES_RU = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь и туман", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    56: "лёгкая ледяная морось", 57: "сильная ледяная морось", 61: "небольшой дождь", 63: "дождь",
    65: "сильный дождь", 66: "небольшой ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
    80: "небольшие ливни", 81: "ливни", 82: "сильные ливни", 85: "небольшие снегопады",
    86: "сильные снегопады", 95: "гроза", 96: "гроза с небольшим градом", 99: "гроза с сильным градом",
}


@dataclass(frozen=True)
class CryptoObservation:
    provider: str
    url: str
    symbol: str
    rub: float | None
    usd: float | None
    updated_at: int | None = None


@dataclass(frozen=True)
class GeoPlace:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str


def _russian(text: str) -> bool:
    return len(_CYR.findall(text or "")) >= 2


def _plain(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", str(value or ""))).replace("\xa0", " ").split())


def _requested_crypto(question: str) -> tuple[str, str, str] | None:
    for coin_id, symbol, label, pattern in _CRYPTO:
        if pattern.search(question):
            return coin_id, symbol, label
    return None


def is_crypto_price_question(question: str) -> bool:
    text = " ".join(str(question or "").split())
    return bool(_requested_crypto(text) and (_PRICE.search(text) or len(text) <= 80))


def is_weather_question(question: str) -> bool:
    return bool(_WEATHER.search(" ".join(str(question or "").split())))


def is_local_time_question(question: str) -> bool:
    return bool(_TIME.search(" ".join(str(question or "").split())))


def is_key_rate_question(question: str) -> bool:
    return bool(_KEY_RATE.search(" ".join(str(question or "").split())))


def is_live_structured_question(question: str) -> bool:
    return bool(
        is_currency_rate_question(question)
        or is_key_rate_question(question)
        or is_crypto_price_question(question)
        or is_weather_question(question)
        or is_local_time_question(question)
    )


def live_structured_label(question: str) -> str:
    if is_currency_rate_question(question): return "Получаю официальный курс Банка России…"
    if is_key_rate_question(question): return "Получаю ключевую ставку Банка России…"
    if is_crypto_price_question(question): return "Получаю и сверяю свежие котировки…"
    if is_weather_question(question): return "Получаю актуальные метеоданные…"
    if is_local_time_question(question): return "Определяю часовой пояс и местное время…"
    return "Получаю актуальные данные…"


def _fmt_money(value: float | None, currency: str) -> str:
    if value is None or not math.isfinite(value) or value <= 0: return ""
    if currency == "RUB":
        return f"{value:,.2f} ₽".replace(",", " ").replace(".", ",")
    return f"${value:,.2f}"


def _spread_ok(values: list[float]) -> bool:
    rows = [value for value in values if math.isfinite(value) and value > 0]
    if len(rows) < 2: return False
    low, high = min(rows), max(rows)
    midpoint = (low + high) / 2
    return midpoint > 0 and (high - low) / midpoint <= 0.06


async def _json_get(client: httpx.AsyncClient, url: str, *, params: dict | None = None) -> tuple[dict, int | None]:
    response = await client.get(url, params=params, headers={"User-Agent": "OLYA-AI/1.0", "Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict): raise ValueError("provider payload is not an object")
    updated = None
    for key in ("last_updated_at", "time"):
        value = payload.get(key)
        if isinstance(value, (int, float)): updated = int(value)
    return payload, updated


async def _crypto_observations(coin_id: str, symbol: str) -> list[CryptoObservation]:
    timeout = httpx.Timeout(2.2, connect=0.8)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        async def coingecko():
            url = "https://api.coingecko.com/api/v3/simple/price"
            try:
                data, _ = await _json_get(client, url, params={"ids": coin_id, "vs_currencies": "rub,usd", "include_last_updated_at": "true"})
                row = data.get(coin_id) if isinstance(data.get(coin_id), dict) else {}
                return CryptoObservation("CoinGecko", url, symbol, float(row.get("rub")) if row.get("rub") else None, float(row.get("usd")) if row.get("usd") else None, int(row.get("last_updated_at")) if row.get("last_updated_at") else None)
            except (httpx.HTTPError, ValueError, TypeError): return None

        async def cryptocompare():
            url = "https://min-api.cryptocompare.com/data/price"
            try:
                data, _ = await _json_get(client, url, params={"fsym": symbol, "tsyms": "USD,RUB"})
                return CryptoObservation("CryptoCompare", url, symbol, float(data.get("RUB")) if data.get("RUB") else None, float(data.get("USD")) if data.get("USD") else None)
            except (httpx.HTTPError, ValueError, TypeError): return None

        async def binance():
            url = "https://api.binance.com/api/v3/ticker/price"
            try:
                data, _ = await _json_get(client, url, params={"symbol": f"{symbol}USDT"})
                return CryptoObservation("Binance", url, symbol, None, float(data.get("price")) if data.get("price") else None)
            except (httpx.HTTPError, ValueError, TypeError): return None

        async def coinbase():
            url = f"https://api.coinbase.com/v2/prices/{symbol}-USD/spot"
            try:
                data, _ = await _json_get(client, url)
                row = data.get("data") if isinstance(data.get("data"), dict) else {}
                return CryptoObservation("Coinbase", url, symbol, None, float(row.get("amount")) if row.get("amount") else None)
            except (httpx.HTTPError, ValueError, TypeError): return None

        raw = await asyncio.gather(coingecko(), cryptocompare(), binance(), coinbase())
    return [row for row in raw if row is not None]


async def _resolve_crypto(question: str) -> TaskExecution | None:
    requested = _requested_crypto(question)
    if requested is None: return None
    coin_id, symbol, label = requested
    rows = await _crypto_observations(coin_id, symbol)
    if len(rows) < 2: return None
    usd_values = [row.usd for row in rows if row.usd]
    if len(usd_values) < 2 or not _spread_ok([float(value) for value in usd_values]): return None
    primary = next((row for row in rows if row.rub and row.usd), rows[0])
    wants_rub = bool(_RUBLE.search(question)) or (_russian(question) and not _DOLLAR.search(question))
    price = _fmt_money(primary.rub if wants_rub and primary.rub else primary.usd, "RUB" if wants_rub and primary.rub else "USD")
    providers = ", ".join(row.provider for row in rows[:4])
    answer = f"Сейчас {label} ({symbol}) стоит примерно {price}. Котировка сверена по независимым источникам: {providers}. Цена меняется в реальном времени."
    sources = [{"title": row.provider, "url": row.url, "domain": quote(row.provider.casefold()), "snippet": f"{symbol}: {_fmt_money(row.usd, 'USD')} {_fmt_money(row.rub, 'RUB')}".strip()} for row in rows[:4]]
    return _build_execution(question, "crypto_price", answer, sources, authoritative=False, fetched=len(rows))


async def _geocode(query: str) -> GeoPlace | None:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": query, "count": 1, "language": "ru", "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.7), trust_env=False) as client:
            response = await client.get(url, params=params, headers={"User-Agent": "OLYA-AI/1.0"})
            response.raise_for_status()
            rows = response.json().get("results") or []
        if not rows: return None
        row = rows[0]
        return GeoPlace(str(row.get("name") or query), str(row.get("country") or ""), float(row["latitude"]), float(row["longitude"]), str(row.get("timezone") or "UTC"))
    except (httpx.HTTPError, KeyError, TypeError, ValueError): return None


def _location_from_question(question: str) -> str:
    text = question.casefold()
    for alias, canonical in _CITY_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text): return canonical
    patterns = (
        r"(?:погода|температура|прогноз)\s+(?:сейчас\s+|сегодня\s+|завтра\s+)?(?:в|для)\s+([А-Яа-яЁёA-Za-z-]{2,40})",
        r"(?:время|час)\s+(?:сейчас\s+)?в\s+([А-Яа-яЁёA-Za-z-]{2,40})",
        r"(?:time|weather|forecast|temperature)\s+(?:now\s+|today\s+|tomorrow\s+)?in\s+([A-Za-z-]{2,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, question, re.I)
        if match: return match.group(1)
    return ""


async def _resolve_weather(question: str) -> TaskExecution | None:
    location = _location_from_question(question)
    if not location: return None
    place = await _geocode(location)
    if place is None: return None
    forecast_url = "https://api.open-meteo.com/v1/forecast"
    wants_tomorrow = bool(_TOMORROW.search(question))
    params = {
        "latitude": place.latitude, "longitude": place.longitude, "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 3,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.3, connect=0.8), trust_env=False) as client:
            response = await client.get(forecast_url, params=params, headers={"User-Agent": "OLYA-AI/1.0"})
            response.raise_for_status(); data = response.json()
    except (httpx.HTTPError, ValueError): return None
    if wants_tomorrow:
        daily = data.get("daily") or {}; index = 1
        try:
            code = int(daily.get("weather_code", [])[index]); low = float(daily.get("temperature_2m_min", [])[index]); high = float(daily.get("temperature_2m_max", [])[index]); rain = daily.get("precipitation_probability_max", [])[index]
        except (IndexError, TypeError, ValueError): return None
        condition = _WEATHER_CODES_RU.get(code, f"код погоды {code}")
        answer = f"Завтра в {place.name}: {condition}, температура примерно от {low:.0f} до {high:.0f} °C" + (f", вероятность осадков до {rain}%" if rain is not None else "") + "."
    else:
        current = data.get("current") or {}
        try:
            temp = float(current["temperature_2m"]); feels = float(current.get("apparent_temperature", temp)); code = int(current.get("weather_code", 0)); wind = float(current.get("wind_speed_10m", 0)); precipitation = float(current.get("precipitation", 0))
        except (KeyError, TypeError, ValueError): return None
        condition = _WEATHER_CODES_RU.get(code, f"код погоды {code}")
        answer = f"Сейчас в {place.name}: {condition}, {temp:.0f} °C, ощущается как {feels:.0f} °C, ветер {wind:.1f} км/ч" + (f", осадки {precipitation:.1f} мм" if precipitation > 0 else "") + "."
    sources = [{"title": "Open-Meteo", "url": forecast_url, "domain": "open-meteo.com", "snippet": f"{place.name}, {place.country}: direct forecast API"}]
    return _build_execution(question, "weather", answer, sources, authoritative=True, fetched=1)


async def _resolve_time(question: str) -> TaskExecution | None:
    location = _location_from_question(question)
    if not location: return None
    place = await _geocode(location)
    if place is None: return None
    try: now = datetime.now(ZoneInfo(place.timezone))
    except Exception: return None
    answer = f"Сейчас в {place.name} {now:%H:%M} ({place.timezone})."
    source = "https://geocoding-api.open-meteo.com/v1/search"
    sources = [{"title": "Open-Meteo Geocoding", "url": source, "domain": "open-meteo.com", "snippet": f"{place.name}: timezone {place.timezone}"}]
    return _build_execution(question, "local_time", answer, sources, authoritative=True, fetched=1)


async def _resolve_key_rate(question: str) -> TaskExecution | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.1, connect=0.8), trust_env=False, follow_redirects=True) as client:
            response = await client.get(_CBR_KEY_RATE_URL, headers={"User-Agent": "Mozilla/5.0 OLYA-AI/1.0"}); response.raise_for_status(); text = response.text
    except httpx.HTTPError: return None
    table_match = re.search(r"<table\b[^>]*>(.*?)</table>", text, re.I | re.S)
    if not table_match: return None
    for row in _ROW.findall(table_match.group(1)):
        cells = [_plain(cell) for cell in _CELL.findall(row)]
        if len(cells) < 2: continue
        date_value = cells[0]; rate_match = re.search(r"\d+(?:[.,]\d+)?", cells[1])
        if not rate_match: continue
        rate = rate_match.group(0).replace(",", ".")
        answer = f"Ключевая ставка Банка России — {rate}% годовых (действует с {date_value})."
        sources = [{"title": "Банк России — ключевая ставка", "url": _CBR_KEY_RATE_URL, "domain": "cbr.ru", "snippet": f"{date_value}: {rate}%"}]
        return _build_execution(question, "key_rate", answer, sources, authoritative=True, fetched=1)
    return None


def _build_execution(question: str, kind: str, answer: str, sources: list[dict], *, authoritative: bool, fetched: int) -> TaskExecution:
    context = [ChatMessage(role="system", content=f"STRUCTURED OFFICIAL FACT. kind={kind}. RESOLVED_ANSWER: {answer}")]
    hosts = {str(row.get("domain") or "") for row in sources if row.get("domain")}
    return TaskExecution(
        plan=TaskSolvePlan(kind=kind, requires_fresh=True, requires_web=True, requires_structured_data=True, user_intent="live structured fact", public_steps=("Получаю актуальные данные", "Проверяю источник", "Возвращаю ответ")),
        context_messages=context, public_sources=sources, searched_results=len(sources), fetched_sources=fetched, failed_fetches=0,
        resolved_answer=answer, authoritative_evidence=authoritative, independent_hosts=len(hosts), evidence_count=len(sources), evidence_words=max(1, len(answer.split())), evidence_chars=len(answer), source_timestamp_present=True,
    )


async def resolve_live_structured_fact(question: str) -> TaskExecution | None:
    started = perf_counter()
    try:
        official = await resolve_structured_fact(question)
        if official is not None: return official
        if is_key_rate_question(question): return await _resolve_key_rate(question)
        if is_crypto_price_question(question): return await _resolve_crypto(question)
        if is_weather_question(question): return await _resolve_weather(question)
        if is_local_time_question(question): return await _resolve_time(question)
        return None
    finally:
        _ = max(0, int((perf_counter() - started) * 1000))
