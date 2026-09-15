from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://|www\.", re.I)
_LEADING_FILLER_RE = re.compile(
    r"^\s*(?:(?:подскажи(?:те)?|скажи(?:те)?|слушай(?:те)?|можешь\s+подсказать|можете\s+подсказать|"
    r"пожалуйста|please|tell\s+me|can\s+you\s+tell\s+me)\s*(?:,|:|-)?\s*)+", re.I,
)
_FRESH_RE = re.compile(
    r"(?:\bсейчас\b|\bщас\b|\bсегодня\b|\bвчера\b|\bзавтра\b|\bпоследн\w*\b|\bактуальн\w*\b|"
    r"\bновост\w*\b|\bтекущ\w*\b|\bнынешн\w*\b|\bкурс\w*\b|\bцен[аы]\b|\bстоимост\w*\b|\bпоч[её]м\b|"
    r"\bпогод\w*\b|\bрасписан\w*\b|\bnow\b|\btoday\b|\blatest\b|\bcurrent\b|\bnews\b|\bprice\b|\bweather\b)", re.I,
)
_EXPLICIT_RECENCY_RE = re.compile(
    r"(?:\bсейчас\b|\bщас\b|\bсегодня\b|\bвчера\b|\bзавтра\b|\bпоследн\w*\b|\bактуальн\w*\b|\bтекущ\w*\b|"
    r"\bнынешн\w*\b|\bnow\b|\btoday\b|\blatest\b|\bcurrent\b|\bnews\b)", re.I,
)
_STABLE_EXPLANATION_RE = re.compile(
    r"^\s*(?:что\s+(?:такое|значит|означает)|как\s+работает|объясни(?:\s+простыми\s+словами)?(?:\s*,?\s+что\s+такое)?|"
    r"в\s+ч[её]м\s+смысл|what\s+is|what\s+does|how\s+does|explain)\b", re.I,
)
_TRANSFORM_RE = re.compile(
    r"^\s*(?:переведи|перепиши|сократи|исправь|отредактируй|улучши|сделай\s+(?:лучше|профессиональнее)|"
    r"translate|rewrite|shorten|proofread|edit|improve)\b", re.I,
)
_CURRENT_ROLE_RE = re.compile(
    r"(?:^|[?.!\s])(?:кто\s+(?:же\s+)?(?:президент|премьер(?:-министр)?|губернатор|мэр|глава|руководитель|"
    r"председатель|генеральный\s+директор|директор|ceo|cto)\b|"
    r"who\s+is\s+(?:the\s+)?(?:president|prime\s+minister|governor|mayor|head|chair|ceo|cto)\b)", re.I,
)
_SHOPPING_LOOKUP_RE = re.compile(
    r"(?:\b(?:посовет\w*|подбер\w*|помоги\s+выбрать|что\s+выбрать)\s+(?:мне\s+)?(?:ноутбук|смартфон|телефон|"
    r"монитор|телевизор|планшет|наушники|роутер|принтер|фотоаппарат|кофемашин\w*|пылесос|холодильник|"
    r"стиральн\w*\s+машин\w*|автомобил\w*|машин\w*|отель|гостиниц\w*|crm|хостинг|vps)\b|"
    r"\b(?:ноутбук|смартфон|телефон|монитор|телевизор|планшет|наушники|роутер|принтер|автомобил\w*)\b.{0,45}\bдо\s*\d[\d\s]*\s*(?:₽|руб|тыс)|"
    r"\b(?:recommend|choose|pick)\s+(?:a\s+)?(?:laptop|phone|monitor|tv|tablet|headphones|router|printer|hotel|crm|hosting)\b)", re.I,
)
_LOCAL_LOOKUP_RE = re.compile(
    r"(?:\bгде\s+(?:поесть|покушать|перекусить|выпить\s+кофе|остановиться|припарковаться)\b|"
    r"\bкуда\s+сходить\b|\bчто\s+посмотреть\s+в\s+[а-яёa-z-]+|"
    r"\b(?:кафе|ресторан\w*|кофейн\w*|аптек\w*|отел\w*|гостиниц\w*)\b.{0,45}\b(?:рядом|поблизости|недалеко|в\s+[а-яёa-z-]+)|"
    r"\b(?:ближайш\w*|где\s+рядом)\s+(?:кафе|ресторан\w*|кофейн\w*|аптек\w*|отел\w*|гостиниц\w*)\b|"
    r"\b(?:restaurant|cafe|hotel|pharmacy)\s+(?:near\s+me|nearby|in\s+[a-z-]+))", re.I,
)
_REGULATED_LOOKUP_RE = re.compile(
    r"(?:\b(?:какие|какой|сколько|размер|ставк\w*|правил\w*|услови\w*)\b.{0,55}\b(?:налог\w*|ндс|ндфл|штраф\w*|пошлин\w*|пенси\w*)\b|"
    r"\b(?:налог\w*|ндс|ндфл|штраф\w*|самозанят\w*|усн|коап|трудов\w*\s+законодательств\w*)\b.{0,55}\b(?:платить|действует|ставк\w*|правил\w*|требован\w*)\b|"
    r"\bкак\s+(?:работает|оформить|перейти\s+на|платить|рассчитать)\s+(?:усн|самозанят\w*|ндс|ндфл|налог\w*)\b|"
    r"\b(?:трудов\w*\s+(?:кодекс|законодательств\w*)|тк\s*рф)\b.{0,70}\b(?:дистанционн\w*|увольнен\w*|отпуск\w*|больничн\w*|работ\w*)\b|"
    r"\bправил\w*\s+(?:въезд\w*|виз\w*|проведен\w*\s+(?:огэ|егэ)|индексац\w*\s+пенси\w*)\b|"
    r"\bкомисси\w*(?:\s+(?:у|для))?\s+(?:ozon|озон|wildberries|вайлдберриз|wb|яндекс\s+маркет\w*)\b|"
    r"\bможно\s+ли\s+(?:вернуть\s+товар|уволить|не\s+платить|получить\s+вычет)\b)", re.I,
)
_DYNAMIC_LOOKUP_RE = re.compile(
    r"(?:\b(?:какая|какой|какие|what)\s+(?:сейчас\s+|щас\s+)?верси\w*\b|"
    r"\b(?:latest|current)\s+version\b|\bдата\s+выхода\b|\bкогда\s+(?:выйдет|релиз|стартует|начн[её]тся)\b|"
    r"\b(?:release\s+date|when\s+(?:will|does).+\brelease)\b|"
    r"\bследующ\w*\s+(?:матч|игр[аы]|бой|гонк|турнир)\b|\bnext\s+(?:match|game|fight|race)\b|"
    r"\bгде\s+(?:сейчас\s+|щас\s+)?купить\b|\b(?:купить|заказать)\s+(?:сейчас\s+|щас\s+)?(?:онлайн|в\s+москве|в\s+россии)?\b|"
    r"\b(?:where\s+to\s+buy|in\s+stock|available\s+now)\b|\bпробк\w*\b|\btraffic\s+(?:now|today)\b)", re.I,
)
_CONTEXT_RE = re.compile(
    r"(?:\bпродолжи\w*\b|\bдальше\b|\bвыше\b|\bпредыдущ\w*\b|\bэтот\b|\bэта\b|\bэти\b|\bэтого\b|\bэтой\b|"
    r"\bтак\s+же\b|\bсделай\s+лучше\b|\bисправь\s+это\b|\bпеределай\b|\bа\s+если\b|\bа\s+почему\b|"
    r"\bкак\s+раньше\b|\bкак\s+выше\b|\bкак\s+в\s+прошл\w*\s+раз\b|\bтот\s+же\s+формат\b|\bв\s+этом\s+чате\b|"
    r"\bcontinue\b|\bprevious\b|\babove\b|\bthis\s+one\b|\bmake\s+it\s+better\b|\bsame\s+format\b)", re.I,
)
_SHORT_FOLLOWUP_RE = re.compile(
    r"^\s*(?:ещ[её]\s+(?:вариант\w*|пример\w*)|другой\s+вариант|короче|подробнее|не\s+так|а\s+теперь|"
    r"что\s+насч[её]т|и\s+ещ[её]|ещ[её]\s+раз|another\s+(?:option|example)|shorter|more\s+detail)\b", re.I,
)
_MEMORY_REFERENCE_RE = re.compile(
    r"(?:\bчто\s+ты\s+(?:помнишь|знаешь)\s+обо?\s+мне\b|\bчто\s+ты\s+знаешь\s+про\s+меня\b|\bты\s+помнишь\b|"
    r"\bкак\s+я\s+(?:просил|просила|говорил|говорила)\b|\bмы\s+(?:решили|обсуждали|договорились)\b|"
    r"\bмои\s+(?:предпочтения|настройки|требования|интересы)\b|\bчто\s+я\s+люблю\b|\bкакие\s+у\s+меня\s+предпочтения\b|"
    r"\bнаша\s+(?:договорённость|договоренность)\b|\bwhat\s+do\s+you\s+remember\s+about\s+me\b|"
    r"\bwhat\s+do\s+you\s+know\s+about\s+me\b|\bdo\s+you\s+remember\b|\bwe\s+decided\b)", re.I,
)
_WORKSPACE_RE = re.compile(
    r"(?:\bнаш\w*\s+проект\b|\bмой\w*\s+проект\b|\bв\s+проекте\b|\bэтот\s+проект\b|"
    r"\bфайл\w*\b|\bдокумент\w*\b|\bрепозитор\w*\b|\bкод\s+проекта\b|\bproject\b|\brepository\b)", re.I,
)
_ATOMIC_RE = re.compile(
    r"^\s*(?:кто\s+(?:написал|автор)\b|кто\s+(?:такой|такая|такие)\b|что\s+(?:такое|значит|означает)\b|"
    r"какая\s+столица\b|какой\s+столицей\b|когда\s+(?:родился|родилась|умер|умерла|основан|основана)\b|"
    r"где\s+(?:родился|родилась|находится)\b|who\s+(?:wrote|is|was)\b|what\s+(?:is|does)\b|"
    r"what\s+is\s+the\s+capital\b|when\s+was\b|where\s+is\b)", re.I,
)
_ANALYTIC_RE = re.compile(
    r"(?:подробн\w*|проанализир\w*|сравни\w*|стратег\w*|архитектур\w*|аудит\w*|исследован\w*|"
    r"план\s+реализац\w*|разработай\w*|analy[sz]e|compare|strategy|audit|research)", re.I,
)
_EXPANSIVE_RE = re.compile(
    r"(?:\bи\s+(?:как|почему|зачем|когда|где|что)\b|\bкак\s+(?:использовать|применять|настроить|сделать|выбрать|начать)\b|"
    r"\bпочему\b|\bплюс\w*\b|\bминус\w*\b|\bпреимуществ\w*\b|\bнедостат\w*\b|\bпример\w*\b|"
    r"\bвариант\w*\b|\bэтап\w*\b|\bшаг\w*\b|\bрекомендац\w*\b|\bпосовет\w*\b|"
    r"\band\s+(?:how|why|when|where|what)\b|\bhow\s+to\b|\bpros?\b|\bcons?\b|\bexamples?\b|\brecommend\w*\b)", re.I,
)


def normalized_question(text: str) -> str:
    value = " ".join(str(text or "").casefold().strip().split())
    previous = None
    while value and previous != value:
        previous = value
        value = _LEADING_FILLER_RE.sub("", value, count=1).strip()
    return value


def _transform_payload_present(text: str) -> bool:
    raw = str(text or ""); normalized = normalized_question(raw)
    if not _TRANSFORM_RE.search(normalized): return False
    if "```" in raw and len(raw) >= 30: return True
    for sep in ("\n", ":"):
        if sep in raw and len(raw.split(sep, 1)[1].strip()) >= 8: return True
    return False


def _has_self_contained_payload(text: str) -> bool:
    raw = str(text or "")
    if _transform_payload_present(raw): return True
    if "```" in raw and len(raw) >= 80: return True
    if "\n" in raw and len(raw.split("\n", 1)[1].strip()) >= 60: return True
    if ":" in raw and len(raw.split(":", 1)[1].strip()) >= 60: return True
    return False


def requires_fresh_data(text: str) -> bool:
    value = normalized_question(text)
    if not value: return False
    if _TRANSFORM_RE.search(value) and _has_self_contained_payload(text) and not _URL_RE.search(value): return False
    if (
        _STABLE_EXPLANATION_RE.search(value) and not _EXPLICIT_RECENCY_RE.search(value) and not _CURRENT_ROLE_RE.search(value)
        and not _DYNAMIC_LOOKUP_RE.search(value) and not _SHOPPING_LOOKUP_RE.search(value)
        and not _LOCAL_LOOKUP_RE.search(value) and not _REGULATED_LOOKUP_RE.search(value)
    ): return False
    return bool(
        _URL_RE.search(value) or _FRESH_RE.search(value) or _CURRENT_ROLE_RE.search(value)
        or _DYNAMIC_LOOKUP_RE.search(value) or _SHOPPING_LOOKUP_RE.search(value)
        or _LOCAL_LOOKUP_RE.search(value) or _REGULATED_LOOKUP_RE.search(value)
    )


def requires_memory_context(text: str) -> bool:
    return bool(_MEMORY_REFERENCE_RE.search(normalized_question(text)))


def requires_conversation_context(text: str) -> bool:
    value = normalized_question(text)
    if not value: return False
    if _MEMORY_REFERENCE_RE.search(value): return True
    if len(value) <= 120 and _SHORT_FOLLOWUP_RE.search(value): return True
    matched = bool(_CONTEXT_RE.search(value))
    if matched and _has_self_contained_payload(text): return False
    return matched


def mentions_workspace_context(text: str) -> bool:
    return bool(_WORKSPACE_RE.search(normalized_question(text)))


def is_atomic_knowledge_question(text: str) -> bool:
    value = normalized_question(text)
    if not value or len(value) > 240: return False
    if requires_fresh_data(value) or _URL_RE.search(value) or _ANALYTIC_RE.search(value) or _EXPANSIVE_RE.search(value): return False
    if requires_conversation_context(value) or requires_memory_context(value) or mentions_workspace_context(value): return False
    return bool(_ATOMIC_RE.search(value))


def is_independent_fast_question(text: str) -> bool:
    value = normalized_question(text)
    if not value or len(value) > 420: return False
    return not (
        requires_conversation_context(value) or requires_memory_context(value) or mentions_workspace_context(value)
        or _ANALYTIC_RE.search(value) or _EXPANSIVE_RE.search(value) or _URL_RE.search(value)
    )


def atomic_output_cap(text: str) -> int | None:
    if not is_atomic_knowledge_question(text): return None
    value = normalized_question(text)
    if re.match(r"^(?:кто\s+(?:написал|автор)|какая\s+столица|какой\s+столицей|when\s+was|what\s+is\s+the\s+capital|who\s+wrote)", value):
        return 96
    return 180
