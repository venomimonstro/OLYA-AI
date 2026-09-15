from __future__ import annotations

import re

from app.schemas.chat import ChatMessage

_EXPLICIT_SHORT = re.compile(r"\b(?:кратко|коротко|в\s+одн(?:у|ой)\s+строк|одним\s+предложением|без\s+объяснений|только\s+ответ|brief|short|one\s+line)\b", re.I)
_ATOMIC = re.compile(r"^\s*(?:кто|что|где|когда|какой|какая|какое|сколько|how\s+much|who|what|where|when)\b", re.I)
_COMPLEX = re.compile(r"\b(?:почему|как\s+лучше|как\s+сделать|объясни|сравни|проанализир|аудит|стратег|план|архитектур|рекоменд|посовет|подбери|вариант|плюс|минус|преимуществ|недостат|причин|решени|инструкц|этап|шаг|roadmap|compare|analy[sz]e|strategy|plan|recommend|explain|how\s+to)\b", re.I)
_LIST_REQUEST = re.compile(r"\b(?:вариант|список|топ|лучши|несколько|примеры|идеи|recommend|options|examples)\b", re.I)


def minimum_chars(question: str) -> int:
    q = " ".join(str(question or "").split())
    if not q or _EXPLICIT_SHORT.search(q):
        return 0
    if _COMPLEX.search(q):
        return 420
    if _LIST_REQUEST.search(q):
        return 320
    if len(q) >= 220:
        return 360
    if _ATOMIC.search(q) and len(q) <= 90:
        return 80
    return 180


def needs_expansion(question: str, answer: str) -> bool:
    target = minimum_chars(question)
    if target <= 0:
        return False
    text = " ".join(str(answer or "").split())
    if len(text) >= target:
        return False
    # Do not force verbosity on a complete numeric/boolean micro-answer.
    if target <= 80 and (re.fullmatch(r"[\d\s.,%+\-—–₽$€]+", text) or text.casefold() in {"да", "нет", "yes", "no"}):
        return False
    return True


def expansion_messages(question: str, answer: str) -> list[ChatMessage]:
    target = minimum_chars(question)
    return [
        ChatMessage(role="system", content=(
            "Ты финальный редактор OLYA AI. Текущий ответ слишком короткий для запроса. "
            "Верни только улучшенный готовый ответ на языке пользователя. Сохрани правильные факты текущего ответа, "
            "не добавляй выдуманные факты, числа, ссылки или проверки. Дай прямой вывод и достаточное объяснение. "
            "Если запрос предполагает выбор/анализ, раскрой критерии, основные варианты и практический вывод. "
            f"Ориентир минимальной содержательности: примерно {target} символов, но без воды и повторов."
        )),
        ChatMessage(role="user", content=f"ЗАПРОС:\n{question[:1800]}\n\nСЛИШКОМ КОРОТКИЙ ОТВЕТ:\n{answer[:3000]}"),
    ]
