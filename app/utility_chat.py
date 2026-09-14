from __future__ import annotations

import ast
import math
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
    "москве": ("Москве", "Europe/Moscow"), "москва": ("Москве", "Europe/Moscow"), "москвы": ("Москве", "Europe/Moscow"),
    "санкт-петербурге": ("Санкт-Петербурге", "Europe/Moscow"), "петербурге": ("Санкт-Петербурге", "Europe/Moscow"), "спб": ("Санкт-Петербурге", "Europe/Moscow"),
    "калининграде": ("Калининграде", "Europe/Kaliningrad"), "калининград": ("Калининграде", "Europe/Kaliningrad"),
    "самаре": ("Самаре", "Europe/Samara"), "самара": ("Самаре", "Europe/Samara"),
    "екатеринбурге": ("Екатеринбурге", "Asia/Yekaterinburg"), "екатеринбург": ("Екатеринбурге", "Asia/Yekaterinburg"),
    "омске": ("Омске", "Asia/Omsk"), "омск": ("Омске", "Asia/Omsk"),
    "новосибирске": ("Новосибирске", "Asia/Novosibirsk"), "новосибирск": ("Новосибирске", "Asia/Novosibirsk"),
    "красноярске": ("Красноярске", "Asia/Krasnoyarsk"), "красноярск": ("Красноярске", "Asia/Krasnoyarsk"),
    "иркутске": ("Иркутске", "Asia/Irkutsk"), "иркутск": ("Иркутске", "Asia/Irkutsk"),
    "владивостоке": ("Владивостоке", "Asia/Vladivostok"), "владивосток": ("Владивостоке", "Asia/Vladivostok"),
}
_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")

_CALC_PREFIX = re.compile(
    r"^\s*(?:сколько\s+будет|посчитай|вычисли|рассчитай|calculate|compute|what\s+is)\s+",
    re.I,
)
_CALC_SUFFIX = re.compile(
    r"\s*(?:[?.!]+)?\s*(?:ответь\s+только\s+числом|только\s+число|answer\s+only\s+with\s+(?:a\s+)?number)?\s*[?.!]*\s*$",
    re.I,
)
_PERCENT_OF = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(.+?)\s*$", re.I)
_ALLOWED_EXPR = re.compile(r"^[\d\s.,+\-*/%()^×÷:xX]+$")


def _city(text: str) -> tuple[str, str] | None:
    normalized = text.casefold()
    for alias, value in _CITY_TIMEZONES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
            return value
    return None


def _eval_arithmetic_node(node: ast.AST, *, depth: int = 0) -> float:
    if depth > 12:
        raise ValueError("expression too deep")
    if isinstance(node, ast.Expression):
        return _eval_arithmetic_node(node.body, depth=depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        value = float(node.value)
        if not math.isfinite(value) or abs(value) > 1e18:
            raise ValueError("number out of range")
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_arithmetic_node(node.operand, depth=depth + 1)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
        left = _eval_arithmetic_node(node.left, depth=depth + 1)
        right = _eval_arithmetic_node(node.right, depth=depth + 1)
        if isinstance(node.op, ast.Add): result = left + right
        elif isinstance(node.op, ast.Sub): result = left - right
        elif isinstance(node.op, ast.Mult): result = left * right
        elif isinstance(node.op, ast.Div): result = left / right
        elif isinstance(node.op, ast.FloorDiv): result = left // right
        elif isinstance(node.op, ast.Mod): result = left % right
        else:
            if abs(right) > 12 or abs(left) > 1e9:
                raise ValueError("power out of range")
            result = left ** right
        if not math.isfinite(result) or abs(result) > 1e18:
            raise ValueError("result out of range")
        return float(result)
    raise ValueError("unsupported expression")


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _calculator_reply(text: str) -> UtilityReply | None:
    raw = " ".join((text or "").strip().split())
    if not raw:
        return None
    candidate = _CALC_PREFIX.sub("", raw, count=1)
    candidate = _CALC_SUFFIX.sub("", candidate, count=1).strip()
    percent = _PERCENT_OF.fullmatch(candidate)
    try:
        if percent:
            pct = float(percent.group(1).replace(",", ".")) / 100.0
            tail = percent.group(2).strip()
            if not _ALLOWED_EXPR.fullmatch(tail):
                return None
            expr = tail.replace(",", ".").replace("×", "*").replace("÷", "/").replace("^", "**")
            expr = re.sub(r"(?<=\d)[xX](?=\d)", "*", expr)
            expr = re.sub(r"(?<=\d):(?=\d)", "/", expr)
            base = _eval_arithmetic_node(ast.parse(expr, mode="eval"))
            return UtilityReply(_format_number(base * pct), "calculator")

        if not _ALLOWED_EXPR.fullmatch(candidate):
            return None
        if not re.search(r"[+\-*/%^×÷:xX]", candidate):
            return None
        expr = candidate.replace(",", ".").replace("×", "*").replace("÷", "/").replace("^", "**")
        expr = re.sub(r"(?<=\d)[xX](?=\d)", "*", expr)
        expr = re.sub(r"(?<=\d):(?=\d)", "/", expr)
        value = _eval_arithmetic_node(ast.parse(expr, mode="eval"))
        return UtilityReply(_format_number(value), "calculator")
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
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

    calculator = _calculator_reply(text)
    if calculator is not None:
        return calculator

    normalized = text.casefold()
    location = _city(text)
    asks_time = any(marker in normalized for marker in _TIME_MARKERS)
    asks_date = any(marker in normalized for marker in _DATE_MARKERS)
    if location and (asks_time or asks_date):
        city_form, timezone_name = location
        now = datetime.now(ZoneInfo(timezone_name))
        if asks_time and asks_date:
            value = f"Сейчас в {city_form} {now:%H:%M}, {now.day} {_MONTHS_RU[now.month - 1]} {now.year} года."
        elif asks_date:
            value = f"Сегодня в {city_form} {now.day} {_MONTHS_RU[now.month - 1]} {now.year} года."
        else:
            value = f"Сейчас в {city_form} {now:%H:%M}."
        return UtilityReply(value, "local_time")
    return None


def mark_utility_request(user_text: str) -> UtilityReply | None:
    reply = utility_reply(user_text)
    _CURRENT_UTILITY.set(reply)
    return reply


def current_utility_reply() -> UtilityReply | None:
    return _CURRENT_UTILITY.get()
