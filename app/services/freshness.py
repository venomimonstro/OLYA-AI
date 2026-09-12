from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FreshnessCategory = Literal[
    "stable",
    "news",
    "weather",
    "market",
    "price",
    "availability",
    "schedule",
    "official_role",
    "law",
    "software_version",
    "recent_general",
]


@dataclass(frozen=True)
class FreshnessDecision:
    required: bool
    category: FreshnessCategory
    reason: str
    max_age_seconds: int
    min_independent_hosts: int
    confidence: float


_STABLE_HISTORY = re.compile(
    r"\b(?:истори[яию]|в\s+\d{4}\s+году|в\s+\d{4}-м|раньше|ранее|histor(?:y|ical)|in\s+\d{4})\b",
    re.IGNORECASE,
)
_STABLE_EXPLANATION = re.compile(
    r"\b(?:что\s+такое|объясни|как\s+работает|принцип|определение|формула|теория|что\s+означает|what\s+is|explain|how\s+does)\b",
    re.IGNORECASE,
)
_DESIGN_TASK = re.compile(
    r"\b(?:придумай|разработай|сформируй|предложи|спроектируй|создай|составь|рассчитай|модель\s+тариф|тарифн\w*\s+сетк|design|create|propose|draft)\b",
    re.IGNORECASE,
)
_RECENCY = re.compile(
    r"\b(?:сейчас|сегодня|на\s+данный\s+момент|актуальн\w*|текущ\w*|последн\w*|свеж\w*|новейш\w*|latest|today|currently|current|right\s+now|recent)\b",
    re.IGNORECASE,
)

_RULES: tuple[tuple[FreshnessCategory, re.Pattern[str], int, int, str], ...] = (
    (
        "recent_general",
        re.compile(r"\b(?:seo[\s-]*аудит|аудит\s+сайта|технический\s+аудит\s+сайта|seo\s+audit|site\s+audit)\b", re.IGNORECASE),
        60 * 60,
        1,
        "Аудит сайта должен опираться на текущее публичное состояние сайта.",
    ),
    (
        "availability",
        re.compile(
            r"\b(?:лучш(?:ий|ая|ее|ие)|топ|рекомендуй|посоветуй|подбери|найди)\b.{0,80}\b(?:стоматолог|клиник|врач|ресторан|кафе|отел|гостиниц|автосервис|салон\s+красоты|фитнес|юрист|адвокат)\w*",
            re.IGNORECASE,
        ),
        6 * 60 * 60,
        2,
        "Локальная рекомендация зависит от текущих компаний, репутации, цен и доступности.",
    ),
    (
        "weather",
        re.compile(r"\b(?:погода|прогноз\s+погоды|температура|осадки|weather|forecast)\b", re.IGNORECASE),
        15 * 60,
        1,
        "Погода быстро меняется и требует свежего источника.",
    ),
    (
        "market",
        re.compile(r"\b(?:курс\s+(?:доллара|евро|юаня|валют)|ключевая\s+ставка|котиров\w*|акци[яий]|индекс\w*|биткоин|bitcoin|ethereum|exchange\s+rate|stock\s+price|market\s+quote|key\s+rate)\b", re.IGNORECASE),
        15 * 60,
        2,
        "Рыночные показатели и ставки изменяются во времени.",
    ),
    (
        "price",
        re.compile(r"\b(?:цена|стоимость|сколько\s+стоит|тариф|прайс|price|cost|pricing)\b", re.IGNORECASE),
        60 * 60,
        2,
        "Текущие цены и тарифы нельзя считать стабильными знаниями модели.",
    ),
    (
        "availability",
        re.compile(r"\b(?:в\s+наличии|наличи[ея]|доступност[ьи]|есть\s+ли\s+места|билеты|available|availability|in\s+stock)\b", re.IGNORECASE),
        15 * 60,
        1,
        "Наличие и доступность меняются оперативно.",
    ),
    (
        "schedule",
        re.compile(r"\b(?:расписани[ея]|время\s+работы|часы\s+работы|во\s+сколько|рейс|поезд|матч|schedule|opening\s+hours|flight|train)\b", re.IGNORECASE),
        60 * 60,
        1,
        "Расписания и часы работы могут измениться.",
    ),
    (
        "law",
        re.compile(r"\b(?:закон|законодательств|постановлен|приказ|норматив|кодекс|штраф|налог|ставка\s+ндс|regulation|law|legal\s+requirement|tax\s+rate)\b", re.IGNORECASE),
        24 * 60 * 60,
        2,
        "Действующие нормы и требования должны подтверждаться актуальными источниками.",
    ),
    (
        "software_version",
        re.compile(r"\b(?:последняя\s+версия|актуальная\s+версия|current\s+version|latest\s+version|релиз|release|версия\s+(?:python|postgres|postgresql|docker|nginx|qwen|llama|node|php|react|fastapi))\b", re.IGNORECASE),
        24 * 60 * 60,
        2,
        "Версии программного обеспечения и моделей меняются.",
    ),
    (
        "official_role",
        re.compile(
            r"\b(?:кто\s+(?:сейчас\s+)?(?:(?:генеральный\s+)?директор|гендиректор|президент|премьер(?:-министр)?|министр|губернатор|мэр|ceo|cto|cfo)|"
            r"нынешн\w*\s+(?:(?:генеральный\s+)?директор|президент|ceo)|current\s+(?:president|prime\s+minister|ceo|director))\b",
            re.IGNORECASE,
        ),
        6 * 60 * 60,
        2,
        "Должности и публичные роли могут измениться.",
    ),
    (
        "news",
        re.compile(r"\b(?:новост[ьи]|что\s+произошло|что\s+случилось|событи[ея]|latest\s+news|breaking\s+news|what\s+happened)\b", re.IGNORECASE),
        30 * 60,
        2,
        "Новости требуют свежих независимых источников.",
    ),
)


def classify_freshness(text: str) -> FreshnessDecision:
    value = " ".join(str(text or "").split())
    if not value:
        return FreshnessDecision(False, "stable", "Пустой запрос.", 0, 0, 1.0)

    historical = bool(_STABLE_HISTORY.search(value))
    recency = bool(_RECENCY.search(value))

    for category, pattern, max_age, min_hosts, reason in _RULES:
        if not pattern.search(value):
            continue
        if historical and not recency:
            return FreshnessDecision(False, "stable", "Запрос явно относится к историческому периоду.", 0, 0, 0.96)
        if _STABLE_EXPLANATION.search(value) and not recency and category in {"price", "market", "law"}:
            return FreshnessDecision(False, "stable", "Запрошено стабильное объяснение понятия, а не текущее значение.", 0, 0, 0.9)
        if _DESIGN_TASK.search(value) and not recency and category == "price":
            return FreshnessDecision(False, "stable", "Пользователь проектирует собственную цену/тариф, а не запрашивает текущую рыночную цену.", 0, 0, 0.92)
        return FreshnessDecision(True, category, reason, max_age, min_hosts, 0.96 if recency else 0.9)

    if recency:
        return FreshnessDecision(
            True,
            "recent_general",
            "Пользователь явно просит актуальное или последнее состояние.",
            24 * 60 * 60,
            2,
            0.86,
        )

    return FreshnessDecision(False, "stable", "Запрос не содержит признаков изменяемого факта.", 0, 0, 0.9)


def needs_fresh_grounding(text: str) -> bool:
    return classify_freshness(text).required
