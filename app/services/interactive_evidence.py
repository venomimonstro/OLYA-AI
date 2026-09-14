from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InteractiveEvidence:
    category: str = "stable"
    evidence_count: int = 0
    independent_hosts: int = 0
    urls: tuple[str, ...] = ()
    authoritative: bool = False
    resolved_answer: str = ""
    source_kind: str = ""

    def satisfies(self, min_hosts: int = 1) -> bool:
        if self.evidence_count <= 0:
            return False
        if self.authoritative:
            return True
        return self.independent_hosts >= max(1, int(min_hosts))


_CURRENT: ContextVar[InteractiveEvidence] = ContextVar(
    "olya_interactive_evidence",
    default=InteractiveEvidence(),
)


def current_interactive_evidence() -> InteractiveEvidence:
    return _CURRENT.get()


def set_interactive_evidence(evidence: InteractiveEvidence) -> Token:
    return _CURRENT.set(evidence)


def reset_interactive_evidence(token: Token) -> None:
    _CURRENT.reset(token)


def _host(url: str) -> str:
    return (urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")


def evidence_from_execution(execution, *, category: str = "stable") -> InteractiveEvidence:
    rows = [row for row in list(getattr(execution, "public_sources", []) or []) if isinstance(row, dict)]
    urls: list[str] = []
    hosts: set[str] = set()
    authoritative = bool(getattr(execution, "authoritative_evidence", False))

    for row in rows:
        trusted = bool(row.get("verified") is True or row.get("search_confirmed") is True or row.get("structured") is True)
        if not trusted:
            continue
        url = str(row.get("url") or "").strip()
        if url and url not in urls:
            urls.append(url)
        host = _host(url)
        if host:
            hosts.add(host)
        kind = str(row.get("source_kind") or "").casefold()
        if kind in {"official", "official_candidate", "structured_official", "primary_official"} and trusted:
            authoritative = True

    fetched = int(getattr(execution, "fetched_sources", 0) or 0)
    fresh = int(getattr(execution, "fresh_evidence_count", 0) or 0)
    confirmed = int(getattr(execution, "search_confirmed_sources", 0) or 0)
    evidence_count = max(fetched, fresh, confirmed, len(urls))
    independent_hosts = max(
        int(getattr(execution, "independent_hosts", 0) or 0),
        int(getattr(execution, "search_independent_hosts", 0) or 0),
        len(hosts),
    )
    return InteractiveEvidence(
        category=str(category or "stable"),
        evidence_count=evidence_count,
        independent_hosts=independent_hosts,
        urls=tuple(urls[:10]),
        authoritative=authoritative,
        resolved_answer=str(getattr(execution, "resolved_answer", "") or ""),
        source_kind=str(getattr(execution, "evidence_source_kind", "") or ""),
    )
