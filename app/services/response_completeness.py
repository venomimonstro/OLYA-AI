from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.chat import ChatMessage

_EXPLICIT_SHORT = re.compile(r"\b(?:кратко|коротко|в\s+одн(?:у|ой)\s+строк|одним\s+предложением|без\s+объяснений|только\s+ответ|brief|short|one\s+line)\b", re.I)
_ATOMIC = re.compile(r"^\s*(?:кто|что|где|когда|какой|какая|какое|сколько|how\s+much|who|what|where|when)\b", re.I)
_COMPLEX = re.compile(r"\b(?:почему|как\s+лучше|как\s+сделать|как\s+выбрать|как\s+реализовать|объясни|сравни|проанализир|аудит|стратег|план|архитектур|рекоменд|посовет|подбери|вариант|плюс|минус|преимуществ|недостат|причин|решени|инструкц|этап|шаг|roadmap|compare|analy[sz]e|strategy|plan|recommend|explain|how\s+to)\b", re.I)
_LIST_REQUEST = re.compile(r"\b(?:вариант|список|топ|лучши|несколько|примеры|идеи|шаги|этапы|recommend|options|examples)\b", re.I)
_DECISION = re.compile(r"\b(?:что\s+лучше|что\s+выбрать|стоит\s+ли|имеет\s+ли\s+смысл|какой\s+вариант|как\s+быть|что\s+делать|выгодн|оптимальн|целесообразн)\b", re.I)
_TECHNICAL = re.compile(r"\b(?:архитектур|сервер|база\s+данных|api\b|индекс|поиск|llm\b|нейросет|код|разработ|реализ|интеграц|оптимизац|безопасност|масштаб)\b", re.I)
_LIST_MARKER = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+", re.M)
_HEADING = re.compile(r"(?:^|\n)\s*(?:#{1,4}\s+|[^\n:]{3,80}:\s*$)", re.M)
_GENERIC_FILLER = re.compile(
    r"\b(?:конечно|безусловно|важно\s+отметить|стоит\s+отметить|в\s+целом|следует\s+отметить|как\s+мы\s+видим)\b",
    re.I,
)


@dataclass(frozen=True)
class AnswerContract:
    min_chars: int
    min_sentences: int = 1
    min_list_items: int = 0
    min_sections: int = 0
    require_conclusion: bool = False


def contract_for(question: str) -> AnswerContract:
    q = " ".join(str(question or "").split())
    if not q or _EXPLICIT_SHORT.search(q):
        return AnswerContract(0)
    complex_request = bool(_COMPLEX.search(q))
    list_request = bool(_LIST_REQUEST.search(q))
    decision = bool(_DECISION.search(q))
    technical = bool(_TECHNICAL.search(q))

    if complex_request and (technical or decision or len(q) >= 140):
        return AnswerContract(900, min_sentences=6, min_sections=2, require_conclusion=True)
    if complex_request:
        return AnswerContract(650, min_sentences=5, min_sections=1, require_conclusion=decision)
    if list_request:
        return AnswerContract(550, min_sentences=4, min_list_items=4)
    if decision:
        return AnswerContract(600, min_sentences=4, require_conclusion=True)
    if len(q) >= 220:
        return AnswerContract(520, min_sentences=4)
    if _ATOMIC.search(q) and len(q) <= 90:
        return AnswerContract(80)
    return AnswerContract(280, min_sentences=2)


def minimum_chars(question: str) -> int:
    return contract_for(question).min_chars


def guidance_message(question: str) -> ChatMessage | None:
    q = " ".join(str(question or "").split())
    contract = contract_for(q)
    if contract.min_chars <= 80 or _EXPLICIT_SHORT.search(q):
        return None

    parts = [
        "Начни с прямого ответа или вывода, а не с приветствия, пересказа вопроса или фразы «Конечно».",
        "Пиши как сильный профильный специалист: естественно, конкретно и без шаблонного нейросетевого тона.",
        "Каждый крупный тезис должен либо объяснять почему, либо давать полезную конкретику, критерий, пример, ограничение или следующий шаг.",
        "Не растягивай текст повторениями и вводными фразами. Структуру используй только там, где она делает ответ понятнее.",
    ]
    if _COMPLEX.search(q):
        parts.append("Для анализа, совета или сравнения раскрой минимум три содержательных аспекта и свяжи их с практическим выводом.")
    if _LIST_REQUEST.search(q):
        parts.append("Если запрошены варианты, топ, примеры или шаги, дай несколько действительно разных пунктов и поясни различия между ними.")
    if _DECISION.search(q):
        parts.append("Если пользователь принимает решение, назови рекомендуемый вариант, критерии выбора, существенные риски и следующий шаг.")
    if _TECHNICAL.search(q):
        parts.append("Для технического запроса дай рабочую архитектуру/алгоритм, ключевые компоненты, ограничения и способ проверки результата.")
    parts.append("Не выдумывай факты, цифры или проверки ради убедительности. Если данных недостаточно, отдели факт от предположения.")
    return ChatMessage(
        role="system",
        content=(
            "OLYA RESPONSE QUALITY: "
            + " ".join(parts)
            + f" Ориентир содержательности: около {contract.min_chars}+ символов, если вопрос действительно требует раскрытия."
        ),
    )


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
    sections = len(_HEADING.findall(text))
    if len(flat) < contract.min_chars:
        return True
    if sentences < contract.min_sentences:
        return True
    if contract.min_list_items and list_items < contract.min_list_items and sentences < contract.min_list_items + 2:
        return True
    if contract.min_sections and sections < contract.min_sections and sentences < contract.min_sentences + 2:
        return True
    if contract.require_conclusion and not _has_conclusion(text):
        return True

    # A long answer can still be low quality if most of its opening is generic
    # filler. Trigger one editorial repair for complex requests in that case.
    if _COMPLEX.search(q):
        opening = flat[:360]
        filler_hits = len(_GENERIC_FILLER.findall(opening))
        if filler_hits >= 3:
            return True
    return False


def expansion_messages(question: str, answer: str) -> list[ChatMessage]:
    contract = contract_for(question)
    requirements = [f"ориентир не менее примерно {contract.min_chars} символов"]
    if contract.min_sentences > 1:
        requirements.append(f"минимум {contract.min_sentences} содержательных предложений/абзацев")
    if contract.min_list_items:
        requirements.append(f"не менее {contract.min_list_items} вариантов/пунктов, если это соответствует запросу")
    if contract.require_conclusion:
        requirements.append("явный практический вывод или рекомендация")

    return [
        ChatMessage(
            role="system",
            content=(
                "Ты финальный редактор OLYA AI. Перепиши ответ так, чтобы он выглядел как работа сильного профильного специалиста, а не шаблон генеративной модели. "
                "Верни только готовый ответ на языке пользователя. Сохрани правильные факты исходного ответа и не добавляй выдуманные цифры, ссылки или проверки. "
                "Начни с сути. Затем объясни причины и критерии. Добавь конкретный пример, сравнение, ограничение или следующий шаг там, где это реально помогает. "
                "Не используй пустые вступления вроде «Конечно», «Важно отметить», «В целом». Не повторяй одну мысль разными словами. "
                "Если запрос предполагает анализ, сравнение или рекомендацию, раскрой минимум три содержательных аспекта и закончи практическим выводом. "
                "Если запрос технический, дай рабочее решение/архитектуру/шаги, ограничения и способ проверки. "
                "Требования качества: " + "; ".join(requirements) + ". Пиши плотно, естественно и по делу."
            ),
        ),
        ChatMessage(role="user", content=f"ЗАПРОС:\n{question[:2600]}\n\nНЕДОСТАТОЧНЫЙ ОТВЕТ:\n{answer[:5500]}"),
    ]
