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

    # Multi-part / professional questions should never collapse to a one-line
    # answer even if the model itself decides to be terse.
    if complex_request and (technical or decision or len(q) >= 140):
        return AnswerContract(700, min_sentences=5, min_list_items=0, min_sections=2, require_conclusion=True)
    if complex_request:
        return AnswerContract(560, min_sentences=4, min_sections=1, require_conclusion=decision)
    if list_request:
        return AnswerContract(480, min_sentences=4, min_list_items=4)
    if decision:
        return AnswerContract(500, min_sentences=4, require_conclusion=True)
    if len(q) >= 220:
        return AnswerContract(460, min_sentences=3)
    if _ATOMIC.search(q) and len(q) <= 90:
        return AnswerContract(80)
    return AnswerContract(240, min_sentences=2)


def minimum_chars(question: str) -> int:
    return contract_for(question).min_chars


def guidance_message(question: str) -> ChatMessage | None:
    q = " ".join(str(question or "").split())
    contract = contract_for(q)
    if contract.min_chars <= 80 or _EXPLICIT_SHORT.search(q):
        return None
    parts: list[str] = []
    if _COMPLEX.search(q):
        parts.append("Для анализа, совета или сравнения раскрой минимум три содержательных аспекта и дай практический вывод.")
    if _LIST_REQUEST.search(q):
        parts.append("Если запрошены варианты, топ, примеры или шаги, дай несколько действительно разных пунктов.")
    if _DECISION.search(q):
        parts.append("Если пользователь принимает решение, назови рекомендуемый вариант и объясни критерии выбора, риски и следующий шаг.")
    if _TECHNICAL.search(q):
        parts.append("Для технического запроса покажи решение, ключевые компоненты/шаги и ограничения, а не только общий тезис.")
    parts.append("Не отвечай одной строкой на многосоставной запрос. Пиши плотно, без воды и повторов, не выдумывай факты ради объёма.")
    return ChatMessage(role="system", content="RESPONSE COMPLETENESS: " + " ".join(parts) + f" Ориентир содержательности: около {contract.min_chars}+ символов, если вопрос требует раскрытия.")


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
    text = str(answer or '').strip()
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
        ChatMessage(role="system", content=(
            "Ты финальный редактор OLYA AI. Текущий ответ недостаточно полный или структурный для запроса. "
            "Верни только улучшенный готовый ответ на языке пользователя. Сохрани правильные факты текущего ответа, "
            "не добавляй выдуманные факты, числа, ссылки или проверки. Дай прямой вывод и достаточное объяснение. "
            "Если запрос предполагает анализ, сравнение или рекомендацию, раскрой минимум 3 содержательных аспекта. "
            "Если запрос технический, дай конкретное решение/архитектуру/шаги и ограничения. "
            "Требования качества: " + "; ".join(requirements) + ". Без воды и искусственного растягивания."
        )),
        ChatMessage(role="user", content=f"ЗАПРОС:\n{question[:2600]}\n\nНЕДОСТАТОЧНЫЙ ОТВЕТ:\n{answer[:5500]}"),
    ]
