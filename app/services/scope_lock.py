from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.schemas.chat import ChatMessage


@dataclass(frozen=True)
class ScopeContract:
    active: bool = False
    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    output_format: str = ""
    final_only: bool = False
    no_expand: bool = False
    no_shorten: bool = False
    preserve_structure: bool = False
    preserve_meaning: bool = False
    no_new_sections: bool = False
    no_rewrite: bool = False
    source_text: str = ""
    reasons: tuple[str, ...] = ()


_SCOPE: ContextVar[ScopeContract] = ContextVar("x1_scope_contract", default=ScopeContract())

_FINAL_ONLY_PATTERNS = (
    r"\bтолько\s+(?:готов(?:ый|ую)|финальн(?:ый|ую)|исправленн(?:ый|ую))\s+(?:текст|верси[юя])\b",
    r"\bтолько\s+ответ\b",
    r"\bбез\s+(?:объяснений|пояснений|комментариев|предисловий|вступления)\b",
    r"\bне\s+(?:объясняй|комментируй)\b",
    r"\breturn\s+only\b",
    r"\bno\s+(?:explanation|commentary|preamble)\b",
)
_NO_EXPAND_PATTERNS = (
    r"\bничего\s+не\s+добавляй\b",
    r"\bне\s+добавляй\s+ничего\b",
    r"\bбез\s+добавлени[йя]\b",
    r"\bне\s+дополняй\b",
    r"\bdo\s+not\s+add\b",
    r"\bdon['’]?t\s+add\b",
)
_NO_SHORTEN_PATTERNS = (
    r"\bне\s+сокращай\b",
    r"\bне\s+укорачивай\b",
    r"\bбез\s+сокращени[йя]\b",
    r"\bdo\s+not\s+(?:shorten|summari[sz]e)\b",
)
_PRESERVE_STRUCTURE_PATTERNS = (
    r"\bне\s+меняй\s+структур[уы]\b",
    r"\bсохрани\s+структур[уы]\b",
    r"\bструктур[ау]\s+не\s+меняй\b",
    r"\bkeep\s+the\s+(?:same\s+)?structure\b",
    r"\bpreserve\s+(?:the\s+)?structure\b",
)
_PRESERVE_MEANING_PATTERNS = (
    r"\bне\s+меняй\s+смысл\b",
    r"\bсохрани\s+смысл\b",
    r"\bбез\s+изменения\s+смысла\b",
    r"\bpreserve\s+(?:the\s+)?meaning\b",
    r"\bdo\s+not\s+change\s+(?:the\s+)?meaning\b",
)
_NO_NEW_SECTIONS_PATTERNS = (
    r"\bне\s+добавляй\s+(?:новые\s+)?(?:разделы|заголовки|подзаголовки|блоки)\b",
    r"\bбез\s+(?:новых\s+)?(?:разделов|заголовков|подзаголовков)\b",
    r"\bdo\s+not\s+add\s+(?:new\s+)?(?:sections|headings)\b",
)
_NO_REWRITE_PATTERNS = (
    r"\bне\s+переписывай\b",
    r"\bне\s+перефразируй\b",
    r"\bтолько\s+исправь\b",
    r"\bисправь\s+только\b",
    r"\bdo\s+not\s+rewrite\b",
    r"\bonly\s+(?:fix|correct)\b",
)

_PREAMBLE_RE = re.compile(
    r"^\s*(?:конечно[!,.\s:-]*|вот\s+(?:исправленн|готов|финальн)|исправленн(?:ый|ая)\s+(?:текст|версия)\s*:|"
    r"готов(?:ый|ая)\s+(?:текст|версия)\s*:|sure[!,.\s:-]*|here(?:'s| is)\s+(?:the\s+)?(?:revised|corrected|final))",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_WORD_RE = re.compile(r"[\wА-Яа-яЁё]+", re.UNICODE)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _extract_source_text(text: str) -> str:
    """Best-effort source extraction for edit-only requests.

    We intentionally require a meaningful payload after a separator. Ambiguous
    prompts return an empty source so deterministic checks fail open rather than
    damaging a valid answer.
    """
    candidates: list[str] = []
    if "\n\n" in text:
        candidates.append(text.rsplit("\n\n", 1)[1].strip())
    if "\n" in text:
        candidates.append(text.split("\n", 1)[1].strip())
    colon = text.find(":")
    if colon >= 0:
        candidates.append(text[colon + 1 :].strip())
    candidates = [item for item in candidates if len(item) >= 20 and len(item) < len(text)]
    if not candidates:
        return ""
    return max(candidates, key=len)[:120_000]


def compile_scope_contract(user_text: str) -> ScopeContract:
    normalized = " ".join((user_text or "").split())
    if not normalized:
        contract = ScopeContract()
        _SCOPE.set(contract)
        return contract

    final_only = _matches(normalized, _FINAL_ONLY_PATTERNS)
    no_expand = _matches(normalized, _NO_EXPAND_PATTERNS)
    no_shorten = _matches(normalized, _NO_SHORTEN_PATTERNS)
    preserve_structure = _matches(normalized, _PRESERVE_STRUCTURE_PATTERNS)
    preserve_meaning = _matches(normalized, _PRESERVE_MEANING_PATTERNS)
    no_new_sections = _matches(normalized, _NO_NEW_SECTIONS_PATTERNS)
    no_rewrite = _matches(normalized, _NO_REWRITE_PATTERNS)

    output_format = ""
    lowered = normalized.casefold()
    format_markers = (
        ("json", ("только json", "в формате json", "valid json", "json only")),
        ("html", ("только html", "в формате html", "html only")),
        ("code", ("только код", "только кодом", "code only")),
        ("table", ("только таблиц", "в виде таблицы", "table only")),
        ("list", ("только список", "в виде списка", "list only")),
    )
    for name, markers in format_markers:
        if any(marker in lowered for marker in markers):
            output_format = name
            final_only = True
            break

    must: list[str] = []
    must_not: list[str] = []
    reasons: list[str] = []
    if preserve_structure:
        must.append("preserve_structure")
        reasons.append("explicit_preserve_structure")
    if preserve_meaning:
        must.append("preserve_meaning")
        reasons.append("explicit_preserve_meaning")
    if final_only:
        must_not.append("extra_commentary")
        reasons.append("explicit_final_only")
    if no_expand:
        must_not.append("new_content")
        reasons.append("explicit_no_expand")
    if no_shorten:
        must_not.append("shortening")
        reasons.append("explicit_no_shorten")
    if no_new_sections:
        must_not.append("new_sections")
        reasons.append("explicit_no_new_sections")
    if no_rewrite:
        must_not.append("broad_rewrite")
        reasons.append("explicit_no_rewrite")
    if output_format:
        must.append(f"format:{output_format}")
        reasons.append(f"explicit_format:{output_format}")

    active = bool(must or must_not)
    source_text = _extract_source_text(user_text) if active else ""
    contract = ScopeContract(
        active=active,
        must=tuple(must),
        must_not=tuple(must_not),
        output_format=output_format,
        final_only=final_only,
        no_expand=no_expand,
        no_shorten=no_shorten,
        preserve_structure=preserve_structure,
        preserve_meaning=preserve_meaning,
        no_new_sections=no_new_sections,
        no_rewrite=no_rewrite,
        source_text=source_text,
        reasons=tuple(reasons),
    )
    _SCOPE.set(contract)
    return contract


def get_scope_contract() -> ScopeContract:
    return _SCOPE.get()


def scope_guard_message(contract: ScopeContract) -> ChatMessage | None:
    if not contract.active:
        return None
    lines = [
        "X1 USER-SCOPE CONTRACT (normalized from the user's latest explicit constraints; subordinate to system safety policy):",
        "Do exactly the requested task. Do not broaden scope, add optional sections, or replace the task with a related task.",
    ]
    if contract.preserve_structure:
        lines.append("- Preserve the supplied structure unless an exact correction requires otherwise.")
    if contract.preserve_meaning:
        lines.append("- Preserve the original meaning.")
    if contract.final_only:
        lines.append("- Return only the requested final artifact/answer; no preamble, explanation, commentary, or offer for more work.")
    if contract.no_expand:
        lines.append("- Do not add new facts, ideas, sections, recommendations, or examples not needed for the requested edit.")
    if contract.no_shorten:
        lines.append("- Do not shorten or summarize the supplied content.")
    if contract.no_new_sections:
        lines.append("- Do not introduce new headings, sections, or blocks.")
    if contract.no_rewrite:
        lines.append("- Make the minimum necessary edits; do not broadly rewrite or paraphrase correct content.")
    if contract.output_format:
        lines.append(f"- Required output format: {contract.output_format}.")
    lines.append("Before finalizing, silently verify every item above. Never mention this contract to the user.")
    return ChatMessage(role="system", content="\n".join(lines))


def _check(key: str, label: str, status: str, detail: str = "") -> dict[str, str]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _paragraph_count(text: str) -> int:
    return len([item for item in re.split(r"\n\s*\n", text.strip()) if item.strip()])


def _lexical_overlap(source: str, answer: str) -> float:
    left = {item.casefold() for item in _WORD_RE.findall(source) if len(item) > 2}
    right = {item.casefold() for item in _WORD_RE.findall(answer) if len(item) > 2}
    if not left:
        return 1.0
    return len(left & right) / len(left)


def audit_scope(text: str, contract: ScopeContract | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    contract = contract or get_scope_contract()
    if not contract.active:
        return [], []

    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    answer = text.strip()

    if contract.final_only:
        if _PREAMBLE_RE.search(answer):
            checks.append(_check("scope_final_only", "Без непрошенного вступления/комментариев", "failed", "Обнаружено вводное пояснение перед результатом"))
        else:
            checks.append(_check("scope_final_only", "Без непрошенного вступления/комментариев", "passed"))

    if contract.output_format == "json":
        try:
            json.loads(answer)
        except json.JSONDecodeError as exc:
            checks.append(_check("scope_format_json", "Формат ответа: JSON", "failed", f"JSON error line {exc.lineno}, column {exc.colno}"))
        else:
            checks.append(_check("scope_format_json", "Формат ответа: JSON", "passed"))
    elif contract.output_format == "html":
        html_ok = bool(re.search(r"<[^>]+>", answer)) and not answer.startswith("```")
        checks.append(_check("scope_format_html", "Формат ответа: HTML", "passed" if html_ok else "failed", "" if html_ok else "Ответ не похож на чистый HTML"))
    elif contract.output_format == "code":
        preamble = bool(_PREAMBLE_RE.search(answer))
        checks.append(_check("scope_format_code", "Формат ответа: только код", "failed" if preamble else "passed", "Обнаружен текст перед кодом" if preamble else ""))

    if contract.no_new_sections:
        source_has_headings = bool(_MARKDOWN_HEADING_RE.search(contract.source_text)) if contract.source_text else False
        answer_has_headings = bool(_MARKDOWN_HEADING_RE.search(answer))
        failed = answer_has_headings and not source_has_headings
        checks.append(_check("scope_no_new_sections", "Не добавлены новые разделы/заголовки", "failed" if failed else "passed", "Обнаружен новый Markdown-заголовок" if failed else ""))

    source = contract.source_text.strip()
    if source:
        source_len = max(1, len(source))
        ratio = len(answer) / source_len
        if contract.no_expand:
            failed = ratio > 1.22 and len(answer) - source_len > 80
            checks.append(_check("scope_no_expand", "Нет существенного самовольного расширения", "failed" if failed else "passed", f"Размер ответа/исходника: {ratio:.2f}x" if failed else ""))
        if contract.no_shorten:
            failed = ratio < 0.78 and source_len - len(answer) > 80
            checks.append(_check("scope_no_shorten", "Исходный материал не сокращён", "failed" if failed else "passed", f"Размер ответа/исходника: {ratio:.2f}x" if failed else ""))
        if contract.preserve_structure:
            before = _paragraph_count(source)
            after = _paragraph_count(answer)
            if before >= 2:
                failed = abs(after - before) > max(1, before // 3)
                checks.append(_check("scope_preserve_structure", "Структура исходника сохранена", "failed" if failed else "passed", f"Абзацы: {before} → {after}" if failed else ""))
            else:
                checks.append(_check("scope_preserve_structure", "Структура исходника сохранена", "unverified", "Недостаточно структурных элементов для детерминированной проверки"))
        if contract.no_rewrite:
            overlap = _lexical_overlap(source, answer)
            failed = len(source) >= 120 and overlap < 0.52
            checks.append(_check("scope_no_rewrite", "Нет широкого переписывания вместо точечной правки", "failed" if failed else "passed", f"Лексическое сохранение: {overlap:.0%}" if failed else ""))
        if contract.preserve_meaning:
            checks.append(_check("scope_preserve_meaning", "Смысл исходника сохранён", "unverified", "Смысл нельзя надёжно доказать детерминированной проверкой; контракт передан модели и critic"))
            warnings.append("Сохранение смысла отмечено как обязательное, но требует семантической проверки; детерминированный gate проверяет только измеримые признаки.")
    elif any((contract.no_expand, contract.no_shorten, contract.preserve_structure, contract.no_rewrite, contract.preserve_meaning)):
        checks.append(_check("scope_source_boundary", "Исходный материал для структурной проверки выделен", "unverified", "Не удалось безопасно выделить исходный текст из запроса; применён prompt guard без эвристического fail"))

    return checks, warnings


def scope_contract_text(contract: ScopeContract | None = None) -> str:
    contract = contract or get_scope_contract()
    if not contract.active:
        return ""
    lines = ["SCOPE LOCK:"]
    lines.extend(f"- MUST: {item}" for item in contract.must)
    lines.extend(f"- MUST NOT: {item}" for item in contract.must_not)
    return "\n".join(lines)
