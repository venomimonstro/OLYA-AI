from __future__ import annotations

import asyncio
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchSource, User
from app.schemas.chat import ChatMessage
from app.services.business_intelligence import build_business_candidates, recommendation_summary
from app.services.discovery import DiscoveryError, SearchHit, cached_provider_search, canonical_result_url, dedupe_hits, enrich_hit
from app.services.freshness import classify_freshness
from app.services.research import source_sha256
from app.services.research_planner import plan_research
from app.services.safety import require_capability

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}]+", re.I)
_WEBSITE_AUDIT_MARKERS = (
    "seo аудит", "seo-аудит", "seo audit", "аудит сайта", "проведи аудит сайта",
    "проанализируй сайт", "анализ сайта", "site audit", "технический аудит сайта",
)
_RECOMMENDATION_MARKERS = (
    "лучший", "лучшая", "лучшие", "лучшее", "найди", "подбери", "посоветуй",
    "рекомендуй", "рекоменд", "топ ", "top ", "сравни",
)
_WEB_ACTION_MARKERS = (
    "найди", "поищи", "проверь в интернете", "поиск в интернете", "посмотри в интернете",
    "свежие данные", "актуальные данные", "актуально сейчас", "цены сейчас", "отзывы",
    "рейтинг", "что сейчас", "сравни варианты", "исследуй рынок", "проведи исследование",
)
_CATEGORY_PATTERNS = (
    (("стоматолог", "зубн"), "стоматология"),
    (("клиник", "медцентр", "медицинск"), "клиника"),
    (("ресторан",), "ресторан"),
    (("кафе", "кофейн"), "кафе"),
    (("отел", "гостиниц"), "отель"),
    (("автосервис", "сто ", "ремонт авто"), "автосервис"),
    (("салон красоты", "парикмахер"), "салон красоты"),
    (("фитнес", "спортзал", "тренажер"), "фитнес-клуб"),
    (("юрист", "адвокат"), "юридическая компания"),
    (("школ", "курс"), "образовательный центр"),
)
_CITY_ALIASES = {
    "москве": "Москва", "москва": "Москва", "москвы": "Москва",
    "санкт-петербурге": "Санкт-Петербург", "петербурге": "Санкт-Петербург", "спб": "Санкт-Петербург",
    "самаре": "Самара", "самара": "Самара", "казани": "Казань", "казань": "Казань",
    "екатеринбурге": "Екатеринбург", "екатеринбург": "Екатеринбург",
    "новосибирске": "Новосибирск", "новосибирск": "Новосибирск",
    "сочи": "Сочи", "уфе": "Уфа", "уфа": "Уфа", "перми": "Пермь", "пермь": "Пермь",
    "тюмени": "Тюмень", "тюмень": "Тюмень", "красноярске": "Красноярск", "красноярск": "Красноярск",
}
_TASK_SOLVER_CONTEXT: ContextVar[tuple[ChatMessage, ...]] = ContextVar("x1_task_solver_context", default=())


@dataclass(frozen=True)
class TaskSolvePlan:
    kind: str
    requires_web: bool
    queries: tuple[str, ...] = ()
    direct_urls: tuple[str, ...] = ()
    location: str = ""
    category: str = ""
    source_mix: tuple[str, ...] = ()
    max_sources: int = 6
    force_freshness: bool = False
    freshness_category: str = "stable"
    freshness_reason: str = ""
    public_steps: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class TaskExecution:
    plan: TaskSolvePlan
    source_ids: list[str] = field(default_factory=list)
    discovered_hits: int = 0
    fetched_sources: int = 0
    failed_fetches: int = 0
    independent_hosts: int = 0
    context_messages: list[ChatMessage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def public_metadata(self) -> dict:
        # Method/results are visible; hidden model reasoning and source bodies are not.
        return {
            "kind": self.plan.kind,
            "web_used": self.plan.requires_web,
            "steps": list(self.plan.public_steps),
            "queries_executed": len(self.plan.queries),
            "discovered_hits": self.discovered_hits,
            "fetched_sources": self.fetched_sources,
            "failed_fetches": self.failed_fetches,
            "independent_hosts": self.independent_hosts,
            "warnings": list(self.warnings[:5]),
        }


def set_task_solver_context(messages: list[ChatMessage]) -> Token:
    return _TASK_SOLVER_CONTEXT.set(tuple(messages))


def reset_task_solver_context(token: Token) -> None:
    _TASK_SOLVER_CONTEXT.reset(token)


def current_task_solver_context() -> list[ChatMessage]:
    return list(_TASK_SOLVER_CONTEXT.get())


def _clean_url(raw: str) -> str:
    return raw.rstrip(".,;:!?)]}\"'")


def _urls(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_clean_url(item) for item in _URL_RE.findall(text)))


def _location(normalized: str) -> str:
    for alias, canonical in _CITY_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
            return canonical
    return ""


def _category(normalized: str) -> str:
    for patterns, category in _CATEGORY_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return category
    return ""


def _origin_aux_urls(url: str) -> tuple[str, ...]:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return (url,)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    return tuple(dict.fromkeys((url, f"{origin}/robots.txt", f"{origin}/sitemap.xml")))


def plan_task(question: str, *, max_queries: int = 4) -> TaskSolvePlan:
    clean = " ".join(str(question or "").split()).strip()
    if not clean:
        return TaskSolvePlan(kind="direct", requires_web=False, reason="empty")
    normalized = clean.casefold()
    urls = _urls(clean)
    max_queries = max(1, min(int(max_queries), 6))

    if urls and any(marker in normalized for marker in _WEBSITE_AUDIT_MARKERS):
        target = urls[0]
        host = (urlsplit(target).hostname or "").removeprefix("www.")
        queries = tuple(q for q in (f"site:{host}" if host else "", f'"{host}" SEO' if host else "") if q)[:max_queries]
        return TaskSolvePlan(
            kind="website_audit",
            requires_web=True,
            queries=queries,
            direct_urls=_origin_aux_urls(target),
            source_mix=("target_site", "robots", "sitemap", "indexed_pages"),
            max_sources=6,
            force_freshness=True,
            freshness_category="website_state",
            freshness_reason="Website audit must observe the current public site rather than rely on model memory.",
            public_steps=("Проверяю сам сайт", "Проверяю robots/sitemap", "Смотрю доступные страницы", "Формирую приоритетный SEO-аудит"),
            reason="website_audit_with_url",
        )

    location = _location(normalized)
    category = _category(normalized)
    recommendation = any(marker in normalized for marker in _RECOMMENDATION_MARKERS)
    if recommendation and category and location:
        base = f"{category} {location}"
        queries = (
            base,
            f"{base} отзывы Яндекс 2ГИС ПроДокторов",
            f"{base} цены специалисты врачи",
            f"{base} лицензия официальный сайт",
        )[:max_queries]
        return TaskSolvePlan(
            kind="local_recommendation",
            requires_web=True,
            queries=queries,
            location=location,
            category=category,
            source_mix=("official", "maps_catalog", "reviews", "pricing", "professional", "independent"),
            max_sources=6,
            force_freshness=True,
            freshness_category="local_business",
            freshness_reason="Local recommendations depend on current businesses, reputation, pricing and availability.",
            public_steps=("Собираю кандидатов", "Сверяю независимые источники", "Сравниваю репутацию и подтверждаемость", "Выбираю наиболее обоснованный вариант"),
            reason="local_recommendation",
        )

    freshness = classify_freshness(clean)
    if freshness.required or any(marker in normalized for marker in _WEB_ACTION_MARKERS):
        planned = plan_research(clean)
        return TaskSolvePlan(
            kind="web_research",
            requires_web=True,
            queries=tuple(planned.queries[:max_queries]),
            source_mix=tuple(planned.source_mix),
            max_sources=5,
            force_freshness=bool(freshness.required),
            freshness_category=freshness.category,
            freshness_reason=freshness.reason,
            public_steps=("Ищу релевантные источники", "Сверяю несколько доменов", "Отделяю подтверждённые факты от предположений", "Формирую итог"),
            reason="fresh_or_explicit_web_task",
        )

    return TaskSolvePlan(kind="direct", requires_web=False, reason="model_answer_sufficient")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def diversify_hits(hits: list[SearchHit], *, kind: str, limit: int) -> list[dict]:
    enriched = [enrich_hit(hit) for hit in dedupe_hits(hits, limit=max(limit * 4, limit))]
    kind_priority = {"official_candidate": 5, "maps_catalog": 4, "reviews": 3, "web": 2}
    enriched.sort(
        key=lambda row: (
            kind_priority.get(str(row.get("source_kind")), 1),
            float(row.get("discovery_score") or 0),
            -int(row.get("rank") or 999),
        ),
        reverse=True,
    )
    result: list[dict] = []
    seen_urls: set[str] = set()
    per_host: dict[str, int] = {}

    if kind == "local_recommendation":
        # Seed different evidence classes first; top search rank is never the full recommendation set.
        for wanted in ("official_candidate", "maps_catalog", "reviews", "web"):
            row = next((r for r in enriched if r.get("source_kind") == wanted and canonical_result_url(str(r.get("url") or "")) not in seen_urls), None)
            if row is None:
                continue
            url = canonical_result_url(str(row["url"]))
            host = _host(url)
            result.append(row)
            seen_urls.add(url)
            per_host[host] = per_host.get(host, 0) + 1
            if len(result) >= limit:
                return result

    for row in enriched:
        url = canonical_result_url(str(row.get("url") or ""))
        if not url or url in seen_urls:
            continue
        host = _host(url)
        max_host = 2 if kind == "website_audit" else 1
        if host and per_host.get(host, 0) >= max_host:
            continue
        result.append(row)
        seen_urls.add(url)
        per_host[host] = per_host.get(host, 0) + 1
        if len(result) >= limit:
            break
    return result


def _store_page(db: Session, *, user_id: str, project_id: str | None, page) -> ResearchSource:
    digest = source_sha256(page.content)
    scope = [
        ResearchSource.user_id == user_id,
        ResearchSource.final_url == page.final_url,
        ResearchSource.content_sha256 == digest,
        ResearchSource.status == "ready",
    ]
    scope.append(ResearchSource.project_id == project_id if project_id else ResearchSource.project_id.is_(None))
    source = db.scalar(select(ResearchSource).where(*scope).order_by(ResearchSource.fetched_at.desc()).limit(1))
    if source is None:
        source = ResearchSource(
            user_id=user_id,
            project_id=project_id,
            url=page.requested_url,
            final_url=page.final_url,
            title=page.title,
            content=page.content,
            content_sha256=digest,
            http_status=page.http_status,
            media_type=page.media_type,
            status="ready",
        )
        db.add(source)
        db.flush()
    return source


def _contract_message(execution: TaskExecution) -> ChatMessage:
    kind = execution.plan.kind
    common = (
        "X1 TASK-SOLVER CONTRACT: The system has already performed the bounded external steps described below. "
        "Answer the user's actual task now; do not ask them to repeat information already inferable from the request or evidence. "
        "Use reasonable low-risk assumptions when they unblock the task and state only assumptions that materially affect the conclusion. "
        "Do not reveal hidden chain-of-thought. A brief method/criteria summary is allowed. "
    )
    if kind == "local_recommendation":
        common += (
            "For a 'best' local business request, search rank is NOT a quality score. Compare several candidates across independent source types, "
            "including official information, maps/catalogs, reviews and professional/price signals when available. "
            "Do not declare a decisive winner when evidence is weak or conflicting; give the strongest shortlist and explain the decision criteria. "
        )
    elif kind == "website_audit":
        common += (
            "For the SEO/site audit, separate directly observed issues from items that require Search Console, analytics, Lighthouse or server access. "
            "Prioritize findings by business impact and implementation effort. Deliver an actionable audit, not a checklist telling the user to audit it themselves. "
        )
    elif kind == "web_research":
        common += "Cross-check important claims across independent domains and surface meaningful conflicts instead of averaging them away. "
    common += f"Executed method: {'; '.join(execution.plan.public_steps)}."
    return ChatMessage(role="system", content=common)


def _discovery_message(execution: TaskExecution, rows: list[dict]) -> ChatMessage | None:
    if not rows:
        return None
    blocks = [
        "UNTRUSTED SEARCH-DISCOVERY SIGNALS. These help identify candidates but are weaker than fetched source snapshots. "
        "Do not treat snippets or search rank as verified facts."
    ]
    for index, row in enumerate(rows[:12], start=1):
        blocks.append(
            f"[DISCOVERY {index}] kind={row.get('source_kind','web')} provider={row.get('provider','')} rank={row.get('rank','')}\n"
            f"Title: {str(row.get('title') or '')[:300]}\nURL: {row.get('url','')}\nSnippet: {str(row.get('snippet') or '')[:500]}"
        )
    if execution.plan.kind == "local_recommendation":
        candidates = build_business_candidates(rows)
        summary = recommendation_summary(candidates)
        blocks.append(
            "DETERMINISTIC COMPARISON SIGNAL (supporting aid, not final truth): "
            + ("evidence supports a potentially decisive leader." if summary["decisive_winner"] else "evidence does not justify an automatic decisive winner.")
        )
        for candidate in candidates[:5]:
            blocks.append(
                f"Candidate: {candidate.display_name}; evidence={candidate.evidence_score:.2f}; "
                f"rating={candidate.public_rating if candidate.public_rating is not None else 'n/a'}; "
                f"review_count_signal={candidate.review_count_total}; independent_source_types={candidate.independent_source_count}; "
                f"comparison={candidate.comparison_state}; conflicts={','.join(candidate.conflict_flags) or 'none'}"
            )
    return ChatMessage(role="user", content="\n\n".join(blocks))


async def execute_task_solver(
    *,
    db: Session,
    user: User,
    settings,
    discovery,
    fetcher,
    question: str,
    project_id: str | None,
) -> TaskExecution:
    plan = plan_task(question, max_queries=int(getattr(settings, "research_max_search_queries", 4)))
    execution = TaskExecution(plan=plan)
    if not plan.requires_web:
        return execution

    require_capability(db, user.id, "research")
    gathered: list[SearchHit] = []
    for query in plan.queries:
        try:
            rows = await cached_provider_search(
                db,
                discovery,
                query,
                count=min(10, int(getattr(settings, "research_max_discovery_results", 20))),
                country="RU",
                language="ru",
                ttl_seconds=int(getattr(settings, "search_cache_ttl_seconds", 3600)),
                quality_mode=(plan.kind == "local_recommendation"),
            )
            gathered.extend(rows)
        except DiscoveryError:
            execution.warnings.append("Поиск временно не дал результатов для одного из запросов")

    gathered = dedupe_hits(gathered, limit=int(getattr(settings, "research_max_discovery_results", 20)))
    execution.discovered_hits = len(gathered)
    selected_rows = diversify_hits(gathered, kind=plan.kind, limit=plan.max_sources)

    urls: list[str] = list(plan.direct_urls)
    for row in selected_rows:
        url = str(row.get("url") or "")
        if url and url not in urls:
            urls.append(url)
    urls = urls[: max(plan.max_sources, len(plan.direct_urls))]

    semaphore = asyncio.Semaphore(3)

    async def fetch_one(url: str):
        async with semaphore:
            return url, await fetcher.fetch(url)

    results = await asyncio.gather(*(fetch_one(url) for url in urls), return_exceptions=True) if urls else []
    source_rows: list[ResearchSource] = []
    failed = 0
    for result in results:
        if isinstance(result, BaseException):
            failed += 1
            continue
        _url, page = result
        try:
            with db.begin_nested():
                source = _store_page(db, user_id=user.id, project_id=project_id, page=page)
            source_rows.append(source)
        except Exception:
            failed += 1
    if source_rows:
        db.commit()
    execution.source_ids = list(dict.fromkeys(source.id for source in source_rows))[:10]
    execution.fetched_sources = len(execution.source_ids)
    execution.failed_fetches = failed
    execution.independent_hosts = len({_host(source.final_url or source.url) for source in source_rows if _host(source.final_url or source.url)})

    execution.context_messages.append(_contract_message(execution))
    discovery_message = _discovery_message(execution, selected_rows)
    if discovery_message is not None:
        execution.context_messages.append(discovery_message)
    if plan.requires_web and not execution.source_ids:
        execution.warnings.append("Не удалось получить ни одного проверяемого снимка источника; итог должен явно обозначить ограничение")
    return execution
