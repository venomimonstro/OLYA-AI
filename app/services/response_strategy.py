from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://|www\.", re.I)
_FRESH_RE = re.compile(
    r"(?:\bсейчас\b|\bсегодня\b|\bвчера\b|\bзавтра\b|\bпоследн\w*\b|\bактуальн\w*\b|"
    r"\bновост\w*\b|\bтекущ\w*\b|\bкурс\w*\b|\bцен[аы]\b|\bстоимост\w*\b|\bпогод\w*\b|"
    r"\bрасписан\w*\b|\bпрезидент\w*\b|\bдиректор\w*\b|\bceo\b|\bnow\b|\btoday\b|"
    r"\blatest\b|\bcurrent\b|\bnews\b|\bprice\b|\bweather\b)",
    re.I,
)
_CONTEXT_RE = re.compile(
    r"(?:\bпродолжи\w*\b|\bдальше\b|\bвыше\b|\bпредыдущ\w*\b|\bэтот\b|\bэта\b|\bэти\b|"
    r"\bэтого\b|\bэтой\b|\bтак\s+же\b|\bсделай\s+лучше\b|\bисправь\s+это\b|\bпеределай\b|"
    r"\bа\s+если\b|\bа\s+почему\b|\bкак\s+раньше\b|\bкак\s+выше\b|\bв\s+этом\s+чате\b|"
    r"\bcontinue\b|\bprevious\b|\babove\b|\bthis\s+one\b|\bmake\s+it\s+better\b)",
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


def requires_fresh_data(text: str) -> bool:
    value = normalized_question(text)
    return bool(value and (_URL_RE.search(value) or _FRESH_RE.search(value)))


def requires_conversation_context(text: str) -> bool:
    value = normalized_question(text)
    if not value:
        return False
    return bool(_CONTEXT_RE.search(value))


def mentions_workspace_context(text: str) -> bool:
    return bool(_WORKSPACE_RE.search(normalized_question(text)))


def is_atomic_knowledge_question(text: str) -> bool:
    value = " ".join(str(text or "").strip().split())
    if not value or len(value) > 240:
        return False
    if requires_fresh_data(value) or _URL_RE.search(value) or _ANALYTIC_RE.search(value):
        return False
    if requires_conversation_context(value) or mentions_workspace_context(value):
        return False
    return bool(_ATOMIC_RE.search(value))


def is_independent_fast_question(text: str) -> bool:
    value = " ".join(str(text or "").strip().split())
    if not value or len(value) > 420:
        return False
    return not (
        requires_conversation_context(value)
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
