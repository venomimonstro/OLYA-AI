from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from time import monotonic
from urllib.parse import urlsplit

from app.schemas.chat import ChatMessage
from app.services.discovery import DiscoveryError, canonical_result_url
from app.services.research import ResearchFetchError, lexical_excerpts
from app.services.response_strategy import normalized_question, requires_fresh_data


_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}]+", re.I)
_TRANSFORM_RE = re.compile(
    r"^(?:переведи|перепиши|сократи|исправь|отредактируй|улучши|сделай\s+(?:лучше|профессиональнее)|"
    r"translate|rewrite|shorten|proofread|edit|improve)\b",
    re.I,
)
_WEB_RE = re.compile(
    r"(?:\bотзыв\w*\b|\bрейтинг\w*\b|\bнайди\b|\bпоищи\b|"
    r"в интернете|в сети|проверь сайт|проанализируй сайт|\bлучши\w*\b|"
    r"\bчто\s+нужно\s+знать\s*,?\s+(?:чтобы|перед)\b|\bсовет\w*\b|\bрекомендац\w*\b|"
    r"\breviews?\b|\brating\b|\bsearch\b|\bfind\b|\brecommend\w*\b)",
    re.I,
)
_VACANCY_LOOKUP_RE = re.compile(
    r"(?:"
    r"\b(?:найди|поищи|покажи|подбери|ищу|найти|поиск|актуальн\w*|открыт\w*)\b.{0,45}\bваканси\w*\b|"
    r"\bваканси\w*\b.{0,60}\b(?:в\s+[А-Яа-яЁёA-Za-z-]+|удален\w*|зарплат\w*|от\s*\d|remote|сейчас|сегодня|актуальн\w*)\b|"
    r"^\s*ваканси\w*\s+(?:python|php|java|маркетолог\w*|дизайнер\w*|разработчик\w*|аналитик\w*|менеджер\w*|seo\b|smm\b).*$"
    r")",
    re.I,
)
_DEEP_WEB_RE = re.compile(
    r"(?:подробн\w*|исследу\w*|сравни\w*|обзор\w*|рынок|конкурент\w*|"
    r"deep research|research|compare|analysis|review)",
    re.I,
)
_ADVICE_WEB_RE = re.compile(
    r"(?:\bчто\s+нужно\s+знать\s*,?\s+(?:чтобы|перед)\b|\bкак\s+(?:лучше|правильно|чаще)\b|"
    r"\bлучши\w*\b|\bсовет\w*\b|\bрекомендац\w*\b|\bчто\s+делать\s*,?\s+чтобы\b|"
    r"\bhow\s+to\b|\badvice\b|\brecommend\w*\b)",
    re.I,
)
_CYR = re.compile(r"[А-Яа-яЁё]")
_PRIVATE_CACHE_RE = re.compile(
    r"(?:\b(?:мой|моя|мо[её]|мои|наш|наша|наше|наши|my|our)\b|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:\+?\d[\d\s()\-]{8,}\d))",
    re.I,
)
_CACHE_FRESH_TTL = 45.0
_CACHE_STABLE_TTL = 300.0
_CACHE_MAX_ENTRIES = 128
_WEB_CACHE: dict[str, tuple[float, "CleanWebResult"]] = {}


@dataclass
class CleanWebResult:
    used: bool = False
    context_messages: list[ChatMessage] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    searched: int = 0
    fetched: int = 0
    failed_fetches: int = 0
    warning: str = ""
    cache_hit: bool = False

    def public_metadata(self) -> dict:
        return {
            "kind": "web_research" if self.used else "direct",
            "web_used": self.used,
            "web_cache_hit": self.cache_hit,
            "searched_results": self.searched,
            "fetched_sources": self.fetched,
            "failed_fetches": self.failed_fetches,
            "sources": self.sources[:5],
            "warnings": [self.warning] if self.warning else [],
            "steps": ["TOP-5 поиска", "Компактные факты", "Один итоговый ответ"] if self.used else [],
        }


def _self_contained_transform(question: str) -> bool:
    raw = str(question or "")
    value = normalized_question(raw)
    if not _TRANSFORM_RE.search(value) or _URL_RE.search(raw):
        return False
    if "```" in raw and len(raw) >= 30:
        return True
    for separator in ("\n", ":"):
        if separator in raw and len(raw.split(separator, 1)[1].strip()) >= 8:
            return True
    return False


def should_use_web(question: str, web_mode: str) -> bool:
    if web_mode == "off":
        return False
    if web_mode == "always":
        return True
    if _self_contained_transform(question):
        return False
    text = " ".join(str(question or "").split())
    return bool(text and (
        _URL_RE.search(text)
        or requires_fresh_data(text)
        or _WEB_RE.search(text)
        or _VACANCY_LOOKUP_RE.search(text)
    ))


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def _clean(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _clone_result(source: CleanWebResult, *, cache_hit: bool) -> CleanWebResult:
    return CleanWebResult(
        used=source.used,
        context_messages=[ChatMessage(role=item.role, content=item.content) for item in source.context_messages],
        sources=[dict(item) for item in source.sources],
        searched=source.searched,
        fetched=source.fetched,
        failed_fetches=source.failed_fetches,
        warning=source.warning,
        cache_hit=cache_hit,
    )


def _cache_key(question: str, web_mode: str, deep: bool) -> str | None:
    raw = str(question or "").strip()
    if web_mode != "auto" or not raw or len(raw) > 240 or _URL_RE.search(raw) or _PRIVATE_CACHE_RE.search(raw):
        return None
    value = normalized_question(raw)
    value = re.sub(r"[^\w\s$€£₽₺₸%./:+-]+", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    if len(value) < 4:
        return None
    return f"{'deep' if deep else 'normal'}:{value}"


def _cache_get(key: str | None, ttl: float) -> CleanWebResult | None:
    if key is None:
        return None
    row = _WEB_CACHE.get(key)
    if row is None:
        return None
    created, result = row
    if monotonic() - created > ttl:
        _WEB_CACHE.pop(key, None)
        return None
    return _clone_result(result, cache_hit=True)


def _cache_put(key: str | None, result: CleanWebResult) -> None:
    if key is None or result.warning or not result.sources:
        return
    _WEB_CACHE[key] = (monotonic(), _clone_result(result, cache_hit=False))
    if len(_WEB_CACHE) > _CACHE_MAX_ENTRIES:
        oldest = min(_WEB_CACHE.items(), key=lambda item: item[1][0])[0]
        _WEB_CACHE.pop(oldest, None)


async def build_clean_web_context(*, discovery, fetcher, question: str, web_mode: str, deep: bool = False) -> CleanWebResult:
    if not should_use_web(question, web_mode):
        return CleanWebResult()

    key = _cache_key(question, web_mode, deep)
    ttl = _CACHE_FRESH_TTL if requires_fresh_data(question) else _CACHE_STABLE_TTL
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    language = "ru" if len(_CYR.findall(question or "")) >= 2 else "en"
    result = CleanWebResult(used=True)
    try:
        hits = await asyncio.wait_for(
            discovery.search(question, count=5, country="RU", language=language),
            timeout=2.6,
        )
    except (DiscoveryError, TimeoutError, asyncio.TimeoutError):
        result.warning = "Поиск сейчас недоступен; актуальные факты не подтверждены."
        result.context_messages = [ChatMessage(
            role="system",
            content="Поиск актуальных данных не ответил. Не выдумывай текущий факт; кратко скажи, что не удалось проверить.",
        )]
        return result

    unique = []
    seen: set[str] = set()
    for hit in hits:
        canonical = canonical_result_url(hit.url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        unique.append(hit)
        if len(unique) >= 5:
            break
    result.searched = len(unique)

    advice = bool(_ADVICE_WEB_RE.search(question or ""))
    deep_read = bool(deep or _DEEP_WEB_RE.search(question or "") or _URL_RE.search(question or ""))
    blocks = ["WEB DATA. Бери факты только отсюда."]
    if advice:
        blocks.append("Вывод + 5–8 практических пунктов; не пересказывай сайты.")

    # TOP-5 remains visible to the user, but only two compact snippets enter the
    # interactive CPU prompt. This is the main TTFT guardrail.
    for index, hit in enumerate(unique, start=1):
        snippet = _clean(hit.snippet, 82)
        domain = _host(hit.url)
        result.sources.append({
            "title": _clean(hit.title, 180) or domain or "Источник",
            "url": hit.url,
            "domain": domain,
            "provider": hit.provider,
            "snippet": snippet,
        })
        if index <= 2:
            blocks.append(f"[{index}] {_clean(hit.title, 48)} | {domain}\n{snippet}")

    # Advice gets at most one opportunistic tiny page excerpt; it may not delay
    # the response by more than 0.65 s. Deep/research may read two fuller pages.
    page_limit = 2 if deep_read else (1 if advice else 0)
    if page_limit and unique:
        page_timeout = 1.8 if deep_read else 0.65
        excerpt_window = 240 if deep_read else 120
        excerpt_limit = 340 if deep_read else 120

        async def fetch_one(index: int, hit):
            try:
                page = await asyncio.wait_for(fetcher.fetch(hit.url), timeout=page_timeout)
                excerpts = lexical_excerpts(page.content, question, limit=1, window=excerpt_window)
                text = "\n".join(excerpt for excerpt, _score in excerpts)[:excerpt_limit]
                return index, hit, text, None
            except (ResearchFetchError, TimeoutError, asyncio.TimeoutError) as exc:
                return index, hit, "", exc

        rows = await asyncio.gather(*(fetch_one(i, hit) for i, hit in enumerate(unique[:page_limit], start=1)))
        for index, hit, text, error in rows:
            if error is not None or not text:
                result.failed_fetches += 1
                continue
            result.fetched += 1
            blocks.append(f"[PAGE {index}] {_host(hit.url)}\n{text}")

    if not unique:
        result.warning = "Поиск не вернул релевантных результатов; актуальные факты не подтверждены."
    context_limit = 1600 if deep_read else 620
    result.context_messages = [ChatMessage(role="system", content="\n\n".join(blocks)[:context_limit])]
    _cache_put(key, result)
    return result
