from __future__ import annotations

import re
from types import SimpleNamespace

from app.services.freshness import classify_freshness

_ATOMIC_FACT = re.compile(
    r"^\s*(?:кто\s+(?:написал|автор|основал|основатель|изобр[её]л|изобретатель|режисс[её]р|снял)|"
    r"какая\s+столица|столица\s+какой|когда\s+(?:родил|умер|создан|основан)|"
    r"who\s+(?:wrote|founded|invented|directed)|what\s+is\s+the\s+capital|when\s+was)\b",
    re.IGNORECASE,
)
_ANALYTIC = re.compile(r"\b(?:подробно|детально|сравни|проанализ|объясни\s+почему|истори|research|compare|analy[sz]e)\w*", re.I)


def is_stable_atomic_fact(question: str) -> bool:
    clean = " ".join(str(question or "").split())
    if not clean or len(clean) > 260 or _ANALYTIC.search(clean):
        return False
    return bool(_ATOMIC_FACT.search(clean) and not classify_freshness(clean).required)


def _settings_proxy(settings):
    data = dict(vars(settings)) if hasattr(settings, "__dict__") else {}
    proxy = SimpleNamespace(**data)
    for name in dir(settings):
        if name.startswith("_") or hasattr(proxy, name):
            continue
        try:
            setattr(proxy, name, getattr(settings, name))
        except Exception:
            pass
    proxy.research_timeout_seconds = min(float(getattr(settings, "research_timeout_seconds", 12.0) or 12.0), 1.6)
    proxy.research_max_search_queries = 1
    proxy.research_max_discovery_results = min(8, max(5, int(getattr(settings, "research_max_discovery_results", 8) or 8)))
    return proxy


class _OnePageFetcher:
    """Allow at most one page download for an atomic stable lookup.

    Search snippets from several independent engines/sites provide discovery;
    one actual page is enough to strengthen the tiny fact without making the
    user wait for three slow websites before the model can answer.
    """

    def __init__(self, inner):
        self.inner = inner
        self.used = False

    async def fetch(self, url: str):
        if self.used:
            raise TimeoutError("atomic fact page budget exhausted")
        self.used = True
        return await self.inner.fetch(url)


def install_atomic_fact_latency_patch() -> None:
    from app.services import fast_web_grounding

    current = fast_web_grounding.execute_fast_web_grounding
    if getattr(current, "_olya_atomic_fact_latency", False):
        return

    async def guarded(*, db, user, settings, discovery, fetcher, question, project_id, force_web=False):
        if not is_stable_atomic_fact(question):
            return await current(
                db=db,
                user=user,
                settings=settings,
                discovery=discovery,
                fetcher=fetcher,
                question=question,
                project_id=project_id,
                force_web=force_web,
            )
        return await current(
            db=db,
            user=user,
            settings=_settings_proxy(settings),
            discovery=discovery,
            fetcher=_OnePageFetcher(fetcher),
            question=question,
            project_id=project_id,
            force_web=force_web,
        )

    guarded._olya_atomic_fact_latency = True  # type: ignore[attr-defined]
    fast_web_grounding.execute_fast_web_grounding = guarded
