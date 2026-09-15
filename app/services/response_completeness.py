from __future__ import annotations

import re

from app.schemas.chat import ChatMessage

_EXPLICIT_SHORT = re.compile(r"\b(?:кратко|коротко|в\s+одн(?:у|ой)\s+строк|одним\s+предложением|без\s+объяснений|только\s+ответ|brief|short|one\s+line)\b", re.I)
_ATOMIC = re.compile(r"^\s*(?:кто|что|где|когда|какой|какая|какое|сколько|how\s+much|who|what|where|when)\b", re.I)
_COMPLEX = re.compile(r"\b(?:почему|как\s+лучше|как\s+сделать|объясни|сравни|проанализир|аудит|стратег|план|архитектур|рекоменд|посовет|подбери|вариант|плюс|минус|преимуществ|недостат|причин|решени|инструкц|этап|шаг|roadmap|compare|analy[sz]e|strategy|plan|recommend|explain|how\s+to)\b", re.I)
_LIST_REQUEST = re.compile(r"\b(?:вариант|список|топ|лучши|несколько|примеры|идеи|шаги|этапы|recommend|options|examples)\b", re.I)
_LIST_MARKER = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+", re.M)


def minimum_chars(question: str) -> int:
    q = " ".join(str(question or "").split())
    if not q or _EXPLICIT_SHORT.search(q):
        return 0
    if _COMPLEX.search(q):
        return 520
    if _LIST_REQUEST.search(q):
        return 420
    if len(q) >= 220:
        return 420
    if _ATOMIC.search(q) and len(q) <= 90:
        return 80
    return 220


def guidance_message(question: str) -> ChatMessage | None:
    q = " ".join(str(question or "").split())
    target = minimum_chars(q)
    if target <= 80 or _EXPLICIT_SHORT.search(q):
        return None
    parts: list[str] = []
    if _COMPLEX.search(q):
        parts.append("Для анализа, совета или сравнения раскрой минимум три содержательных аспекта и дай практический вывод.")
    if _LIST_REQUEST.search(q):
        parts.append("Если запрошены варианты, топ, примеры или шаги, дай несколько действительно разных пунктов.")
    parts.append("Не отвечай одной строкой на многосоставной запрос. Пиши плотно, без воды и повторов, не выдумывай факты ради объёма.")
    return ChatMessage(role="system", content="RESPONSE COMPLETENESS: " + " ".join(parts) + f" Ориентир содержательности: около {target}+ символов, если вопрос требует раскрытия.")


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+|\n+", text) if len(part.strip()) >= 8])


def needs_expansion(question: str, answer: str) -> bool:
    q = " ".join(str(question or "").split())
    target = minimum_chars(q)
    if target <= 0:
        return False
    text = str(answer or '').strip()
    flat = " ".join(text.split())
    if not flat:
        return True
    if target <= 80 and (re.fullmatch(r"[\d\s.,%+\-—–₽$€]+", flat) or flat.casefold() in {"да", "нет", "yes", "no"}):
        return False
    complex_request = bool(_COMPLEX.search(q))
    list_request = bool(_LIST_REQUEST.search(q))
    sentences = _sentence_count(text)
    list_items = len(_LIST_MARKER.findall(text))
    if len(flat) < target:
        return True
    if complex_request and sentences < 3:
        return True
    if list_request and list_items < 3 and sentences < 5:
        return True
    if not _ATOMIC.search(q) and len(flat) < 360 and sentences < 2:
        return True
    return False


def expansion_messages(question: str, answer: str) -> list[ChatMessage]:
    target = minimum_chars(question)
    return [
        ChatMessage(role="system", content=(
            "Ты финальный редактор OLYA AI. Текущий ответ недостаточно полный или структурный для запроса. "
            "Верни только улучшенный готовый ответ на языке пользователя. Сохрани правильные факты текущего ответа, "
            "не добавляй выдуманные факты, числа, ссылки или проверки. Дай прямой вывод и достаточное объяснение. "
            "Если запрос предполагает анализ, сравнение или рекомендацию, раскрой минимум 3 содержательных аспекта и практический вывод. "
            "Если пользователь просит варианты/топ/список, дай несколько действительно разных пунктов. "
            f"Ориентир содержательности: не менее примерно {target} символов, но без воды, повторов и искусственного растягивания."
        )),
        ChatMessage(role="user", content=f"ЗАПРОС:\n{question[:2200]}\n\nНЕДОСТАТОЧНЫЙ ОТВЕТ:\n{answer[:4500]}"),
    ]
