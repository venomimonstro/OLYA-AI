from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.chat import ChatMessage

_EXPLICIT_SHORT = re.compile(r"\b(?:кратко|коротко|в\s+одн(?:у|ой)\s+строк|одним\s+предложением|без\s+объяснений|только\s+ответ|brief|short|one\s+line)\b", re.I)
_ATOMIC = re.compile(r"^\s*(?:кто|что|где|когда|какой|какая|какое|сколько|how\s+much|who|what|where|when)\b", re.I)
_COMPLEX = re.compile(r"\b(?:почему|как\s+лучше|как\s+сделать|как\s+выбрать|как\s+реализовать|объясни|сравни|проанализир|анализ|аудит|стратег|план|архитектур|рекоменд|посовет|подбери|вариант|плюс|минус|преимуществ|недостат|причин|решени|инструкц|этап|шаг|roadmap|compare|analy[sz]e|strategy|plan|recommend|explain|how\s+to)\b", re.I)
_LIST_REQUEST = re.compile(r"\b(?:вариант|список|топ|лучши|несколько|примеры|идеи|шаги|этапы|recommend|options|examples)\b", re.I)
_DECISION = re.compile(r"\b(?:что\s+лучше|что\s+выбрать|стоит\s+ли|имеет\s+ли\s+смысл|какой\s+вариант|как\s+быть|что\s+делать|выгодн|оптимальн|целесообразн)\b", re.I)
_TECHNICAL = re.compile(r"\b(?:архитектур|сервер|база\s+данных|api\b|индекс|поиск|llm\b|нейросет|код|разработ|реализ|интеграц|оптимизац|безопасност|масштаб)\b", re.I)
_RESEARCH = re.compile(r"\b(?:исслед|рынок|конкурент|тренд|актуальн|сейчас|сегодня|последн|рейтинг|отзыв|цена|стоимост|характеристик)\b", re.I)
_LIST_MARKER = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+", re.M)
_GENERIC_FILLER = re.compile(r"\b(?:конечно|безусловно|важно\s+отметить|стоит\s+отметить|в\s+целом|следует\s+отметить|как\s+мы\s+видим|на\s+самом\s+деле)\b", re.I)
_REASON_WORDS = re.compile(r"\b(?:потому\s+что|поскольку|причин|это\s+да[её]т|за\s+сч[её]т|означает|поэтому|следовательно|почему)\b", re.I)
_LIMIT_WORDS = re.compile(r"\b(?:огранич|риск|минус|недостат|но\b|однако|зависит|исключени|оговорк|компромисс|trade[- ]?off)\b", re.I)
_ACTION_WORDS = re.compile(r"\b(?:следующ|сделать|проверь|начать|использ|настро|выбрать|рекоменд|шаг|действ|попроб|сравнить|измер)\b", re.I)
_SPECIFICITY = re.compile(r"(?:\d|\bнапример\b|\bесли\b|\bкритери|\bметрик|\bпорог|\bвариант|\bсценар|\bархитектур|\bкомпонент|\bэтап|\bendpoint|\bttl\b|\bredis\b)", re.I)


@dataclass(frozen=True)
class AnswerContract:
    min_chars: int
    min_sentences: int = 1
    min_list_items: int = 0
    require_conclusion: bool = False
    require_reasoning: bool = False
    require_limitations: bool = False
    require_action: bool = False
    require_specificity: bool = False


def contract_for(question: str) -> AnswerContract:
    q = " ".join(str(question or "").split())
    if not q or _EXPLICIT_SHORT.search(q):
        return AnswerContract(0)
    complex_request = bool(_COMPLEX.search(q))
    list_request = bool(_LIST_REQUEST.search(q))
    decision = bool(_DECISION.search(q))
    technical = bool(_TECHNICAL.search(q))
    research = bool(_RESEARCH.search(q))

    if complex_request and (technical or decision or research or len(q) >= 140):
        return AnswerContract(
            900, min_sentences=7, require_conclusion=True,
            require_reasoning=True, require_limitations=True, require_action=True, require_specificity=True,
        )
    if complex_request:
        return AnswerContract(
            700, min_sentences=5, require_conclusion=decision,
            require_reasoning=True, require_action=True, require_specificity=True,
        )
    if list_request:
        return AnswerContract(560, min_sentences=4, min_list_items=4, require_specificity=True)
    if decision:
        return AnswerContract(
            650, min_sentences=5, require_conclusion=True,
            require_reasoning=True, require_limitations=True, require_action=True,
        )
    if len(q) >= 220:
        return AnswerContract(560, min_sentences=4, require_reasoning=True, require_specificity=True)
    if _ATOMIC.search(q) and len(q) <= 90:
        return AnswerContract(80)
    return AnswerContract(320, min_sentences=3, require_specificity=True)


def minimum_chars(question: str) -> int:
    return contract_for(question).min_chars


def guidance_message(question: str) -> ChatMessage | None:
    q = " ".join(str(question or "").split())
    contract = contract_for(q)
    if contract.min_chars <= 80 or _EXPLICIT_SHORT.search(q):
        return None
    parts = [
        "Начни с прямого ответа/вывода. Не трать первые абзацы на приветствие, пересказ вопроса или общие слова.",
        "Работай как сильный профильный специалист: сначала пойми практическую цель пользователя, затем отвечай именно на неё.",
        "Не ограничивайся перечислением фактов. Объясняй причинно-следственные связи: почему это так и что из этого следует.",
        "Давай конкретику: критерии, числа из проверенных данных, сценарии, примеры, архитектуру или последовательность действий — в зависимости от задачи.",
        "Отделяй подтверждённый факт от вывода и предположения. Не придумывай факты, цифры, источники или проверки ради убедительности.",
        "Если в WEB EVIDENCE есть актуальные данные, синтезируй их в собственный ответ, а не пересказывай выдачу.",
        "Пиши естественно и плотно. Заголовки используй только когда они реально улучшают читаемость; хороший связный ответ без заголовков тоже допустим.",
    ]
    if _COMPLEX.search(q):
        parts.append("Для анализа раскрой минимум три независимых содержательных аспекта, затем свяжи их в единый практический вывод.")
    if _LIST_REQUEST.search(q):
        parts.append("Если нужны варианты, они должны реально отличаться; для каждого дай смысл выбора, а не только название.")
    if _DECISION.search(q):
        parts.append("Для решения укажи рекомендуемый путь, критерии выбора, существенные риски/компромиссы и следующий шаг.")
    if _TECHNICAL.search(q):
        parts.append("Для технической задачи дай рабочую схему: компоненты, поток данных/алгоритм, отказоустойчивость или ограничения и способ проверить результат.")
    if _RESEARCH.search(q):
        parts.append("Для актуальной/исследовательской задачи используй найденные данные для проверки тезисов и явно не выдавай устаревшее знание за текущий факт.")
    return ChatMessage(role="system", content="OLYA QUALITY CONTRACT: " + " ".join(parts))


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+|\n+", text) if len(part.strip()) >= 8])


def _has_conclusion(text: str) -> bool:
    low = str(text or "").casefold()
    return any(token in low for token in ("итог", "вывод", "рекоменд", "поэтому", "оптималь", "лучше", "я бы выбрал", "следующий шаг"))


def needs_expansion(question: str, answer: str) -> bool:
    q = " ".join(str(question or "").split())
    contract = contract_for(q)
    if contract.min_chars <= 0:
        return False
    text = str(answer or "").strip()
    flat = " ".join(text.split())
    if not flat:
        return True
    if contract.min_chars <= 80 and (re.fullmatch(r"[\d\s.,%+\-—–₽$€]+", flat) or flat.casefold() in {"да", "нет", "yes", "no"}):
        return False
    sentences = _sentence_count(text)
    list_items = len(_LIST_MARKER.findall(text))
    if len(flat) < contract.min_chars:
        return True
    if sentences < contract.min_sentences:
        return True
    if contract.min_list_items and list_items < contract.min_list_items and sentences < contract.min_list_items + 2:
        return True
    if contract.require_conclusion and not _has_conclusion(text):
        return True
    if contract.require_reasoning and not _REASON_WORDS.search(flat):
        return True
    if contract.require_limitations and not _LIMIT_WORDS.search(flat):
        return True
    if contract.require_action and not _ACTION_WORDS.search(flat):
        return True
    if contract.require_specificity and not _SPECIFICITY.search(flat):
        return True
    if _COMPLEX.search(q) and len(_GENERIC_FILLER.findall(flat[:420])) >= 2:
        return True
    return False


def expansion_messages(question: str, answer: str) -> list[ChatMessage]:
    contract = contract_for(question)
    requirements = [f"ориентир примерно {contract.min_chars}+ символов без искусственного растягивания"]
    if contract.min_sentences > 1:
        requirements.append(f"минимум {contract.min_sentences} содержательных предложений/абзацев")
    if contract.min_list_items:
        requirements.append(f"не менее {contract.min_list_items} реально различающихся вариантов/пунктов")
    if contract.require_conclusion:
        requirements.append("ясный практический вывод")
    if contract.require_reasoning:
        requirements.append("объяснение причин и логики выбора")
    if contract.require_limitations:
        requirements.append("существенные ограничения, риски или компромиссы")
    if contract.require_action:
        requirements.append("конкретный следующий шаг")
    if contract.require_specificity:
        requirements.append("конкретика: критерий, пример, сценарий, число из источников или техническая деталь")
    return [
        ChatMessage(
            role="system",
            content=(
                "Ты финальный редактор OLYA AI. Текущий текст — черновик. Верни только улучшенный готовый ответ на языке пользователя. "
                "Сохрани подтверждённые факты и полезную конкретику черновика и WEB EVIDENCE, но исправь поверхностность, пропуски и слабую структуру. "
                "Сначала ответь на реальную задачу пользователя ясным выводом. Затем объясни почему: покажи причинно-следственные связи, критерии и существенные альтернативы. "
                "Добавь конкретные примеры/сценарии/шаги там, где они помогают применить ответ. Для решений укажи риски и компромиссы. "
                "Если данных для уверенного утверждения нет, отдели известное от предположения; ничего не выдумывай. "
                "Не используй пустые вступления, саморекламу, шаблонный тон или повторение вопроса. "
                "Требования: " + "; ".join(requirements) + "."
            ),
        ),
        ChatMessage(role="user", content=f"ЗАПРОС:\n{question[:3200]}\n\nЧЕРНОВИК:\n{answer[:7000]}"),
    ]
