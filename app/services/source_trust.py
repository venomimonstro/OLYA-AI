from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


_STRONG_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?|rules?)\b", re.I),
    re.compile(r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\s*[:=]", re.I),
    re.compile(r"\byou\s+are\s+(?:chatgpt|an?\s+ai|an?\s+assistant|the\s+assistant)\b", re.I),
    re.compile(r"\b(?:do|must)\s+not\s+(?:mention|reveal|cite|follow)\b.{0,100}\b(?:source|instruction|policy|prompt)\b", re.I | re.S),
    re.compile(r"\b(?:выполни|игнорируй|забудь)\b.{0,100}\b(?:инструкц|правил|промпт|системн)\w*", re.I | re.S),
    re.compile(r"\b(?:системн(?:ый|ого)|developer)\s+(?:промпт|инструкц)\w*\s*[:=]", re.I),
)
_LINE_NORMALIZER = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)


@dataclass(frozen=True)
class SourceTrust:
    host: str
    score: int
    quarantined: bool
    flags: tuple[str, ...]


def source_host(url: str) -> str:
    host = (urlsplit(str(url)).hostname or "").rstrip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _repetition_risk(content: str) -> bool:
    if len(content) < 4000:
        return False
    lines = [
        _LINE_NORMALIZER.sub(" ", line).strip().casefold()
        for line in content.splitlines()
        if len(line.strip()) >= 40
    ]
    if lines:
        counts: dict[str, int] = {}
        for line in lines:
            counts[line] = counts.get(line, 0) + 1
        if max(counts.values(), default=0) >= 5:
            return True
    tokens = [item.casefold() for item in _TOKEN_RE.findall(content[:120_000])]
    if len(tokens) >= 1000:
        unique_ratio = len(set(tokens)) / max(len(tokens), 1)
        if unique_ratio < 0.08:
            return True
    return False


def assess_source(url: str, content: str) -> SourceTrust:
    """Cheap deterministic RAG-poisoning screen.

    This never decides whether a factual claim is politically/semantically true.
    It only detects structural signals that make a page unsafe to promote to
    verified grounding: control-prompt directives, extreme repetition and raw-IP
    hosting. Suspicious sources may still be stored for audit, but must not become
    evidence that upgrades an answer to ``supported``.
    """
    host = source_host(url)
    flags: list[str] = []
    score = 100
    sample = content[:160_000]
    injection_hits = sum(1 for pattern in _STRONG_INJECTION_PATTERNS if pattern.search(sample))
    if injection_hits:
        flags.append("embedded_ai_control_directive")
        score -= min(80, 55 + max(0, injection_hits - 1) * 10)
    if _repetition_risk(sample):
        flags.append("extreme_content_repetition")
        score -= 30
    if host and _is_ip_literal(host):
        flags.append("raw_ip_source")
        score -= 20
    if not host:
        flags.append("missing_source_host")
        score = 0
    score = max(0, min(100, score))
    quarantined = "embedded_ai_control_directive" in flags or score < 50
    return SourceTrust(host=host, score=score, quarantined=quarantined, flags=tuple(flags))


def source_is_fresh(fetched_at: datetime | None, *, max_age_hours: float, now: datetime | None = None) -> bool:
    if fetched_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    stamp = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=timezone.utc)
    return 0 <= (current - stamp).total_seconds() <= max(1.0, float(max_age_hours)) * 3600.0


def sanitize_excerpt(text: str) -> str:
    """Neutralize source-embedded control lines while preserving factual prose."""
    output: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if any(pattern.search(line) for pattern in _STRONG_INJECTION_PATTERNS):
            output.append("[X1 quarantined an instruction-like line from this untrusted source]")
        else:
            output.append(raw)
    return "\n".join(output).strip()
