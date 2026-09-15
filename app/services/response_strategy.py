from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://|www\.", re.I)
_FRESH_RE = re.compile(
    r"(?:\bсейчас\b|\bсегодня\b|\bвчера\b|\bзавтра\b|\bпоследн\w*\b|\bактуальн\w*\b|"
    r"\bновост\w*\b|\bтекущ\w*\b|\bнынешн\w*\b|\bкурс\w*\b|\bцен[аы]\b|\bстоимост\w*\b|"
    r"\bпогод\w*\b|\bрасписан\w*\b|\bnow\b|\btoday\b|\blatest\b|\bcurrent\b|\bnews\b|"
    r"\bprice\b|\bweather\b)",
    re.I,
)
_EXPLICIT_RECENCY_RE = re.compile(
    r"(?:\bсейчас\b|\bсегодня\b|\bвчера\b|\bзавтра\b|\bпоследн\w*\b|\bактуальн\w*\b|\bтекущ\w*\b|"
    r"\bнынешн\w*\b|\bnow\b|\btoday\b|\blatest\b|\bcurrent\b|\bnews\b)",
    re.I,
)
_STABLE_EXPLANATION_RE = re.compile(
    r"^\s*(?:что\s+(?:такое|значит|означает)|как\s+работает|объясни(?:\s+простыми\s+словами)?(?:\s*,?\s+что\s+такое)?|"
    r"в\s+ч[её]м\s+смысл|what\s+is|what\s+does|how\s+does|explain)\b",
    re.I,
)
_CURRENT_ROLE_RE = re.compile(
    r"(?:^|[?.!\s])(?:кто\s+(?:же\s+)?(?:президент|премьер(?:-министр)?|губернатор|мэр|"
    r"генеральный\s+директор|директор|ceo|cto)\b|"
    r"who\s+is\s+(?:the\s+)?(?:president|prime\s+minister|governor|mayor|ceo|cto)\b)",
    re.I,
)
_DYNAMIC_LOOKUP_RE = re.compile(
    r"(?:\b(?:какая|какой|какие|what)\s+(?:сейчас\s+)?верси\w*\b|"
    r"\b(?:latest|current)\s+version\b|\bдата\s+выхода\b|\bкогда\s+(?:выйдет|релиз|стартует|начн[её]тся)\b|"
    r"\b(?:release\s+date|when\s+(?:will|does).+\brelease)\b|"
    r"\bследующ\w*\s+(?:матч|игр[аы]|бой|гонк|турнир)\b|\bnext\s+(?:match|game|fight|race)\b|"
    r"\bгде\s+(?:сейчас\s+)?купить\b|\b(?:купить|заказать)\s+(?:сейчас\s+)?(?:онлайн|в\s+москве|в\s+россии)?\b|"
    r"\b(?:where\s+to\s+buy|in\s+stock|available\s+now)\b|\bпробк\w*\b|\btraffic\s+(?:now|today)\b)",
    re.I,
)
_CONTEXT_RE = re.compile(
    r"(?:\bпродолжи\w*\b|\bдальше\b|\bвыше\b|\bпредыдущ\w*\b|\bэтот\b|\bэта\b|\bэти\b|"
    r"\bэтого\b|\bэтой\b|\bтак\s+же\b|\bсделай\s+лучше\b|\bисправь\s+это\b|\bпеределай\b|"
    r"\bа\s+если\b|\bа\s+почему\b|\bкак\s+раньше\b|\bкак\s+выше\b|\bкак\s+в\s+прошл\w*\s+раз\b|"
    r"\bтот\s+же\s+формат\b|\bв\s+этом\s+чате\b|\bcontinue\b|\bprevious\b|\babove\b|"
    r"\bthis\s+one\b|\bmake\s+it\s+better\b|\bsame\s+format\b)",
    re.I,
)
_MEMORY_REFERENCE_RE = re.compile(
    r"(?:\bчто\s+ты\s+(?:помнишь|знаешь)\s+обо?\s+мне\b|\bты\s+помнишь\b|"
    r"\bкак\s+я\s+(?:просил|просила|говорил|говорила)\b|\bмы\s+(?:решили|обсуждали|договорились)\b|"
    r"\bмои\s+(?:предпочтения|настройки|требования)\b|\bнаша\s+(?:договорённость|договоренность)\b|"
    r"\bwhat\s+do\s+you\s+remember\s+about\s+me\b|\bdo\s+you\s+remember\b|\bwe\s+decided\b)",
    re.I,
)
_WORKSPACE_RE = re.compile(
    r"(?:\bнаш\w*\s+проект\b|\bмой\w*\s+проект\b|\bв\s+проекте\b|\bэтот\s+проект\b|"
    r"\bфайл\w*\b|\bдокумент\w*\b|\bрепозитор\w*\b|\bкод\s+проекта\b|\bproject\b|\brepository\b)",
    re.I,
)
_ATOMIC_RE = re.compile(
    r"^\s*(?:"
    r"кто\s+(?:написал|автор)\b|"
    r"кто\s+(?:такой|такая|такие)\b|"
    r"что\s+(?:такое|значит|означает)\b|"
    r"какая\s+столица\b|какой\s+столицей\b|"
    r"когда\s+(?:родился|родилась|умер|умерла|основан|основана)\b|"
    r"где\s+(?:родился|родилась|находится)\b|"
    r"who\s+(?:wrote|is|was)\b|what\s+(?:is|does)\b|"
    r"what\s+is\s+the\s+capital\b|when\s+was\b|where\s+is\b"
    r")",
    re.I,
)
_ANALYTIC_RE = re.compile(
    r"(?:подробн\w*|проанализир\w*|сравни\w*|стратег\w*|архитектур\w*|аудит\w*|"
    r"исследован\w*|план\s+реализац\w*|разработай\w*|analy[sz]e|compare|strategy|audit|research)",
    re.I,
)


def normalized_question(text: str) -> str:
    return " ".join(str(text or "").casefold().strip().split())


def _has_self_contained_payload(text: str) -> bool:
    raw = str(text or "")
    if "```" in raw and len(raw) >= 80:
        return True
    if "\n" in raw:
        tail = raw.split("\n", 1)[1].strip()
        if len(tail) >= 60:
            return True
    if ":" in raw:
        tail = raw.split(":", 1)[1].strip()
        if len(tail) >= 60:
            return True
    return False


def requires_fresh_data(text: str) -> bool:
    value = normalized_question(text)
    if not value:
        return False
    if _STABLE_EXPLANATION_RE.search(value) and not _EXPLICIT_RECENCY_RE.search(value) and not _CURRENT_ROLE_RE.search(value) and not _DYNAMIC_LOOKUP_RE.search(value):
        return False
    return bool(_URL_RE.search(value) or _FRESH_RE.search(value) or _CURRENT_ROLE_RE.search(value) or _DYNAMIC_LOOKUP_RE.search(value))


def requires_memory_context(text: str) -> bool:
    return bool(_MEMORY_REFERENCE_RE.search(normalized_question(text)))


def requires_conversation_context(text: str) -> bool:
    value = normalized_question(text)
    if not value:
        return False
    if _MEMORY_REFERENCE_RE.search(value):
        return True
    matched = bool(_CONTEXT_RE.search(value))
    if matched and _has_self_contained_payload(text):
        return False
    return matched


def mentions_workspace_context(text: str) -> bool:
    return bool(_WORKSPACE_RE.search(normalized_question(text)))


def is_atomic_knowledge_question(text: str) -> bool:
    value = " ".join(str(text or "").strip().split())
    if not value or len(value) > 240:
        return False
    if requires_fresh_data(value) or _URL_RE.search(value) or _ANALYTIC_RE.search(value):
        return False
    if requires_conversation_context(value) or requires_memory_context(value) or mentions_workspace_context(value):
        return False
    return bool(_ATOMIC_RE.search(value))


def is_independent_fast_question(text: str) -> bool:
    value = " ".join(str(text or "").strip().split())
    if not value or len(value) > 420:
        return False
    return not (
        requires_conversation_context(value)
        or requires_memory_context(value)
        or mentions_workspace_context(value)
        or _ANALYTIC_RE.search(value)
        or _URL_RE.search(value)
    )


def atomic_output_cap(text: str) -> int | None:
    if not is_atomic_knowledge_question(text):
        return None
    value = normalized_question(text)
    if re.match(r"^(?:кто\s+(?:написал|автор)|какая\s+столица|какой\s+столицей|when\s+was|what\s+is\s+the\s+capital|who\s+wrote)", value):
        return 96
    return 180
