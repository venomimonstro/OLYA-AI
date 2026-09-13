from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.identity_patch import creator_reply


@dataclass(frozen=True)
class UtilityReply:
    text: str
    kind: str


_CURRENT_UTILITY: ContextVar[UtilityReply | None] = ContextVar("x1_current_utility_reply", default=None)

_GREETING_RU = re.compile(r"^\s*(?:привет|здравствуй|здравствуйте|доброе\s+утро|добрый\s+день|добрый\s+вечер|хай)\s*[!?.…]*\s*$", re.I)
_GREETING_EN = re.compile(r"^\s*(?:hi|hello|hey)\s*[!?.…]*\s*$", re.I)
_TIME_MARKERS = ("который час", "сколько времени", "какое время", "текущее время", "время сейчас", "сейчас время")
_DATE_MARKERS = ("какая сегодня дата", "какое сегодня число", "текущая дата", "дата сегодня")
_CITY_TIMEZONES = {
    "москве": ("Москва", "Europe/Moscow"), "москва": ("Москва", "Europe/Moscow"), "москвы": ("Москва", "Europe/Moscow"),
    "санкт-петербурге": ("Санкт-Петербург", "Europe/Moscow"), "петербурге": ("Санкт-Петербург", "Europe/Moscow"), "спб": ("Санкт-Петербург", "Europe/Moscow"),
    "калининграде": ("Калининград", "Europe/Kaliningrad"), "калининград": ("Калининград", "Europe/Kaliningrad"),
    "самаре": ("Самара", "Europe/Samara"), "самара": ("Самара", "Europe/Samara"),
    "екатеринбурге": ("Екатеринбург", "Asia/Yekaterinburg"), "екатеринбург": ("Екатеринбург", "Asia/Yekaterinburg"),
    "омске": ("Омск", "Asia/Omsk"), "омск": ("Омск", "Asia/Omsk"),
    "новосибирске": ("Новосибирск", "Asia/Novosibirsk"), "новосибирск": ("Новосибирск", "Asia/Novosibirsk"),
    "красноярске": ("Красноярск", "Asia/Krasnoyarsk"), "красноярск": ("Красноярск", "Asia/Krasnoyarsk"),
    "иркутске": ("Иркутск", "Asia/Irkutsk"), "иркутск": ("Иркутск", "Asia/Irkutsk"),
    "владивостоке": ("Владивосток", "Asia/Vladivostok"), "владивосток": ("Владивосток", "Asia/Vladivostok"),
}
_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _city(text: str) -> tuple[str, str] | None:
    normalized = text.casefold()
    for alias, value in _CITY_TIMEZONES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
            return value
    return None


def utility_reply(user_text: str) -> UtilityReply | None:
    text = " ".join((user_text or "").strip().split())
    if not text:
        return None

    identity = creator_reply(text)
    if identity is not None:
        return UtilityReply(identity, "identity")
    if _GREETING_RU.fullmatch(text):
        return UtilityReply("Привет! Чем могу помочь?", "greeting")
    if _GREETING_EN.fullmatch(text):
        return UtilityReply("Hello! How can I help?", "greeting")

    normalized = text.casefold()
    location = _city(text)
    asks_time = any(marker in normalized for marker in _TIME_MARKERS)
    asks_date = any(marker in normalized for marker in _DATE_MARKERS)
    if location and (asks_time or asks_date):
        city_name, timezone_name = location
        now = datetime.now(ZoneInfo(timezone_name))
        if asks_time and asks_date:
            value = f"Сейчас в {city_name} {now:%H:%M}, {now.day} {_MONTHS_RU[now.month - 1]} {now.year} года."
        elif asks_date:
            value = f"Сегодня в {city_name} {now.day} {_MONTHS_RU[now.month - 1]} {now.year} года."
        else:
            value = f"Сейчас в {city_name} {now:%H:%M}."
        return UtilityReply(value, "local_time")
    return None


def mark_utility_request(user_text: str) -> UtilityReply | None:
    reply = utility_reply(user_text)
    _CURRENT_UTILITY.set(reply)
    return reply


def current_utility_reply() -> UtilityReply | None:
    return _CURRENT_UTILITY.get()
