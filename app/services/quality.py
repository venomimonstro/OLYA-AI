from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.chat import AnswerRequirement, ChatMessage
from app.services.scope_lock import audit_scope, get_scope_contract, scope_contract_text

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b", re.IGNORECASE),
    re.compile(r"\{\{[^{}]{1,120}\}\}"),
    re.compile(r"\[(?:вставить|insert|placeholder)[^\]]{0,120}\]", re.IGNORECASE),
)
_URL_RE = re.compile(r"https?://[^\s<>()\]\[\]{}\"']+", re.IGNORECASE)
_FRESHNESS_MARKERS = (
    "сегодня", "сейчас", "на данный момент", "актуальн", "последние новости", "последние данные",
    "последняя версия", "текущая цена", "текущая стоимость", "текущий курс", "latest", "today",
    "right now", "currently", "current price", "current rate", "latest version", "latest news",
    "курс доллара", "курс евро", "курс валют", "обменный курс", "цена биткоин", "цена bitcoin",
    "стоимость биткоин", "котиров", "биржев", "погода", "прогноз погоды", "расписание", "в наличии",
    "наличие товара", "доступность билетов", "доступные билеты", "exchange rate", "bitcoin price",
    "crypto price", "stock price", "market quote", "weather", "forecast", "schedule", "in stock", "ticket availability",
)


def needs_fresh_grounding(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(marker in normalized for marker in _FRESHNESS_MARKERS)


@dataclass(frozen=True)
class DeterministicAudit:
    checks: list[dict[str, Any]]
    warnings: list[str]

    @property
    def failed(self) -> bool:
        return any(item["status"] == "failed" for item in self.checks)

    @property
    def unverifiable(self) -> bool:
        return any(item["status"] == "unverified" for item in self.checks)

    @property
    def grounded(self) -> bool:
        return any(item.get("key") == "source_grounding" and item.get("status") == "passed" for item in self.checks)


class AnswerQualityEngine:
    """Cheap deterministic gates plus conditional critic/repair."""

    def deterministic(self, text: str, requirements: list[AnswerRequirement], verified_urls: set[str] | None = None, *, freshness_required: bool = False) -> DeterministicAudit:
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []
        checks.append(self._check("non_empty", "Ответ не пустой", "passed" if text.strip() else "failed", "" if text.strip() else "Модель вернула пустой ответ"))

        placeholder = next((pattern.search(text) for pattern in _PLACEHOLDER_PATTERNS if pattern.search(text)), None)
        checks.append(self._check("no_placeholders", "Нет служебных заглушек", "failed" if placeholder else "passed", placeholder.group(0) if placeholder else ""))

        for index, requirement in enumerate(requirements, start=1):
            status, detail = self._evaluate_requirement(text, requirement)
            checks.append(self._check(f"requirement_{index}_{requirement.kind}", requirement.label or self._default_label(requirement), status, detail))

        scope_checks, scope_warnings = audit_scope(text)
        checks.extend(scope_checks)
        warnings.extend(scope_warnings)
        # Auto verification historically repairs only when payload.requirements is
        # truthy. Scope Lock is itself an explicit user requirement, so add a
        # harmless internal sentinel to the same list. This makes a failed scope
        # gate enter the existing single repair pass without extra inference on a
        # successful answer. The sentinel is added after normal requirement checks.
        if get_scope_contract().active and not requirements:
            requirements.append(AnswerRequirement(kind="min_chars", value=1, label="Внутренний Scope Lock активен"))

        urls = sorted(set(item.rstrip(".,;:!?)]}") for item in _URL_RE.findall(text)))
        allowed = {item.rstrip("/") for item in (verified_urls or set())}
        cited_allowed = {item.rstrip("/") for item in urls} & allowed
        if allowed:
            if cited_allowed:
                checks.append(self._check("source_grounding", "Ответ действительно ссылается на проверенный source context", "passed", f"Процитировано проверенных source URL: {len(cited_allowed)}"))
            else:
                checks.append(self._check("source_grounding", "Приложенные источники должны быть явно процитированы в ответе", "unverified", "Источник был в prompt, но ни один проверенный URL не процитирован в финальном ответе"))
                warnings.append("К ответу были приложены источники, но финальный текст не содержит ссылку ни на один проверенный snapshot URL.")
        if urls:
            unverified = [item for item in urls if item.rstrip("/") not in allowed]
            if unverified:
                checks.append(self._check("external_urls", "Все внешние ссылки происходят из загруженных источников", "unverified", f"Неподтверждённых ссылок: {len(unverified)}"))
                warnings.append("Ответ содержит URL, которых нет среди проверенных снимков источников.")
            else:
                checks.append(self._check("external_urls", "Все внешние ссылки происходят из загруженных источников", "passed", f"Подтверждено ссылок: {len(urls)}"))
        if freshness_required:
            if cited_allowed:
                checks.append(self._check("freshness_grounding", "Актуальные утверждения ссылаются на проверенный свежий источник", "passed", f"Процитировано свежих source URL: {len(cited_allowed)}"))
            else:
                checks.append(self._check("freshness_grounding", "Актуальные утверждения требуют явно процитированного свежего источника", "unverified", "В финальном ответе нет ссылки на проверенный свежий research snapshot"))
                warnings.append("Запрос зависит от актуальных данных, но финальный ответ не процитировал проверенный свежий источник; текущие факты не считаются подтверждёнными.")
        return DeterministicAudit(checks=checks, warnings=warnings)

    def critic_messages(self, user_request: str, answer: str, requirements: list[AnswerRequirement]) -> list[ChatMessage]:
        requirement_lines = "\n".join(f"- {item.label or self._default_label(item)}" for item in requirements) or "- Явных формальных требований нет"
        scope_lines = scope_contract_text() or "- Нет отдельного Scope Lock"
        return [
            ChatMessage(role="system", content=("Ты внутренний критик X1. Не переписывай ответ и не утверждай, что факты проверены. Найди только явные противоречия запросу, нарушения Scope Lock, пропущенные требования, внутренние противоречия и неподтверждённые утверждения. Верни только JSON: " + '{"issues":[{"severity":"critical|major|minor","message":"..."}],"summary":"..."}. Если явных проблем нет, issues должен быть пустым массивом.')),
            ChatMessage(role="user", content=f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_request}\n\nФОРМАЛЬНЫЕ ТРЕБОВАНИЯ:\n{requirement_lines}\n\n{scope_lines}\n\nОТВЕТ X1:\n{answer}"),
        ]

    def repair_messages(self, user_request: str, answer: str, deterministic: DeterministicAudit, requirements: list[AnswerRequirement]) -> list[ChatMessage]:
        failures = [item for item in deterministic.checks if item["status"] == "failed"]
        failure_lines = "\n".join(f"- {item['label']}: {item.get('detail', '')}" for item in failures)
        requirement_lines = "\n".join(f"- {item.label or self._default_label(item)}" for item in requirements) or "- Нет дополнительных формальных требований"
        scope_lines = scope_contract_text() or "- Нет отдельного Scope Lock"
        return [
            ChatMessage(role="system", content="Ты редактор X1. Исправь только перечисленные дефекты ответа. Строго соблюдай Scope Lock. Не добавляй новые факты без необходимости, не меняй уже правильные части и не обсуждай проверку. Верни только исправленный финальный ответ."),
            ChatMessage(role="user", content=f"ИСХОДНЫЙ ЗАПРОС:\n{user_request}\n\nТРЕБОВАНИЯ:\n{requirement_lines}\n\n{scope_lines}\n\nНАЙДЕННЫЕ ДЕФЕКТЫ:\n{failure_lines}\n\nТЕКУЩИЙ ОТВЕТ:\n{answer}"),
        ]

    def parse_critic(self, raw: str) -> dict[str, Any]:
        candidate = raw.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return {"ok": False, "issues": [], "summary": "Critic returned invalid JSON"}
        issues = data.get("issues")
        if not isinstance(issues, list):
            return {"ok": False, "issues": [], "summary": "Critic response has invalid issues format"}
        clean: list[dict[str, str]] = []
        for issue in issues[:20]:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity", "minor")).lower()
            if severity not in {"critical", "major", "minor"}:
                severity = "minor"
            message = str(issue.get("message", "")).strip()[:1000]
            if message:
                clean.append({"severity": severity, "message": message})
        return {"ok": True, "issues": clean, "summary": str(data.get("summary", ""))[:2000]}

    def final_status(self, deterministic: DeterministicAudit, critic: dict[str, Any] | None) -> str:
        if deterministic.failed:
            return "failed"
        if deterministic.unverifiable:
            return "unverified"
        if critic is None:
            return "checked"
        if not critic.get("ok"):
            return "unverified"
        issues = critic.get("issues", [])
        if any(item.get("severity") in {"critical", "major"} for item in issues):
            return "failed"
        if issues:
            return "checked"
        return "supported" if deterministic.grounded else "checked"

    @staticmethod
    def _check(key: str, label: str, status: str, detail: str = "") -> dict[str, str]:
        return {"key": key, "label": label, "status": status, "detail": detail}

    @staticmethod
    def _default_label(requirement: AnswerRequirement) -> str:
        value = requirement.value
        return {"contains": f"Ответ содержит: {value}", "not_contains": f"Ответ не содержит: {value}", "max_chars": f"Ответ не длиннее {value} символов", "min_chars": f"Ответ не короче {value} символов", "valid_json": "Ответ является корректным JSON"}[requirement.kind]

    @staticmethod
    def _evaluate_requirement(text: str, requirement: AnswerRequirement) -> tuple[str, str]:
        if requirement.kind == "contains":
            needle = str(requirement.value).casefold(); return (("passed", "") if needle in text.casefold() else ("failed", f"Не найдено: {requirement.value}"))
        if requirement.kind == "not_contains":
            needle = str(requirement.value).casefold(); return (("passed", "") if needle not in text.casefold() else ("failed", f"Найден запрещённый фрагмент: {requirement.value}"))
        if requirement.kind == "max_chars":
            limit = int(requirement.value); return (("passed", "") if len(text) <= limit else ("failed", f"{len(text)} > {limit}"))
        if requirement.kind == "min_chars":
            limit = int(requirement.value); return (("passed", "") if len(text) >= limit else ("failed", f"{len(text)} < {limit}"))
        if requirement.kind == "valid_json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return "failed", f"JSON error at line {exc.lineno}, column {exc.colno}"
            return "passed", ""
        raise ValueError(f"Unsupported requirement kind: {requirement.kind}")
