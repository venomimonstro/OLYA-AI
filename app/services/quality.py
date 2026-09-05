from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.chat import AnswerRequirement, ChatMessage


_PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b", re.IGNORECASE),
    re.compile(r"\{\{[^{}]{1,120}\}\}"),
    re.compile(r"\[(?:вставить|insert|placeholder)[^\]]{0,120}\]", re.IGNORECASE),
)
_URL_RE = re.compile(r"https?://[^\s<>()\]\[\]{}\"']+", re.IGNORECASE)


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


class AnswerQualityEngine:
    """Cheap quality gates that never ask the model to verify facts it generated.

    Deterministic checks may fail an answer. They never claim that free-form factual
    prose is true. A model critic can only lower confidence or mark an answer as
    supported; it cannot produce X1's strongest verification status.
    """

    def deterministic(
        self, text: str, requirements: list[AnswerRequirement], verified_urls: set[str] | None = None
    ) -> DeterministicAudit:
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []

        if not text.strip():
            checks.append(self._check("non_empty", "Ответ не пустой", "failed", "Модель вернула пустой ответ"))
        else:
            checks.append(self._check("non_empty", "Ответ не пустой", "passed"))

        placeholder = next((pattern.search(text) for pattern in _PLACEHOLDER_PATTERNS if pattern.search(text)), None)
        if placeholder:
            checks.append(self._check("no_placeholders", "Нет служебных заглушек", "failed", placeholder.group(0)))
        else:
            checks.append(self._check("no_placeholders", "Нет служебных заглушек", "passed"))

        for index, requirement in enumerate(requirements, start=1):
            key = f"requirement_{index}_{requirement.kind}"
            label = requirement.label or self._default_label(requirement)
            status, detail = self._evaluate_requirement(text, requirement)
            checks.append(self._check(key, label, status, detail))

        urls = sorted(set(item.rstrip(".,;:!?)]}") for item in _URL_RE.findall(text)))
        if urls:
            allowed = {item.rstrip("/") for item in (verified_urls or set())}
            unverified = [item for item in urls if item.rstrip("/") not in allowed]
            if unverified:
                checks.append(
                    self._check(
                        "external_urls",
                        "Все внешние ссылки происходят из загруженных источников",
                        "unverified",
                        f"Неподтверждённых ссылок: {len(unverified)}",
                    )
                )
                warnings.append("Ответ содержит URL, которых нет среди проверенных снимков источников.")
            else:
                checks.append(
                    self._check(
                        "external_urls",
                        "Все внешние ссылки происходят из загруженных источников",
                        "passed",
                        f"Подтверждено ссылок: {len(urls)}",
                    )
                )

        return DeterministicAudit(checks=checks, warnings=warnings)

    def critic_messages(self, user_request: str, answer: str, requirements: list[AnswerRequirement]) -> list[ChatMessage]:
        requirement_lines = "\n".join(f"- {item.label or self._default_label(item)}" for item in requirements) or "- Явных формальных требований нет"
        return [
            ChatMessage(
                role="system",
                content=(
                    "Ты внутренний критик X1. Не переписывай ответ и не утверждай, что факты проверены. "
                    "Найди только явные противоречия запросу, пропущенные требования, внутренние противоречия "
                    "и места, где ответ делает неподтверждённое утверждение. Верни только JSON: "
                    '{"issues":[{"severity":"critical|major|minor","message":"..."}],"summary":"..."}. '
                    "Если явных проблем нет, issues должен быть пустым массивом."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_request}\n\n"
                    f"ФОРМАЛЬНЫЕ ТРЕБОВАНИЯ:\n{requirement_lines}\n\n"
                    f"ОТВЕТ X1:\n{answer}"
                ),
            ),
        ]

    def repair_messages(
        self,
        user_request: str,
        answer: str,
        deterministic: DeterministicAudit,
        requirements: list[AnswerRequirement],
    ) -> list[ChatMessage]:
        failures = [item for item in deterministic.checks if item["status"] == "failed"]
        failure_lines = "\n".join(
            f"- {item['label']}: {item.get('detail', '')}" for item in failures
        )
        requirement_lines = "\n".join(
            f"- {item.label or self._default_label(item)}" for item in requirements
        ) or "- Нет дополнительных формальных требований"
        return [
            ChatMessage(
                role="system",
                content=(
                    "Ты редактор X1. Исправь только перечисленные дефекты ответа. "
                    "Не добавляй новые факты без необходимости, не меняй уже правильные части и не обсуждай проверку. "
                    "Верни только исправленный финальный ответ."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"ИСХОДНЫЙ ЗАПРОС:\n{user_request}\n\n"
                    f"ТРЕБОВАНИЯ:\n{requirement_lines}\n\n"
                    f"НАЙДЕННЫЕ ДЕФЕКТЫ:\n{failure_lines}\n\n"
                    f"ТЕКУЩИЙ ОТВЕТ:\n{answer}"
                ),
            ),
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
            # A critic cannot turn unverified sources into verified evidence.
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
        return "supported"

    @staticmethod
    def _check(key: str, label: str, status: str, detail: str = "") -> dict[str, str]:
        return {"key": key, "label": label, "status": status, "detail": detail}

    @staticmethod
    def _default_label(requirement: AnswerRequirement) -> str:
        value = requirement.value
        labels = {
            "contains": f"Ответ содержит: {value}",
            "not_contains": f"Ответ не содержит: {value}",
            "max_chars": f"Ответ не длиннее {value} символов",
            "min_chars": f"Ответ не короче {value} символов",
            "valid_json": "Ответ является корректным JSON",
        }
        return labels[requirement.kind]

    @staticmethod
    def _evaluate_requirement(text: str, requirement: AnswerRequirement) -> tuple[str, str]:
        if requirement.kind == "contains":
            needle = str(requirement.value).casefold()
            return ("passed", "") if needle in text.casefold() else ("failed", f"Не найдено: {requirement.value}")
        if requirement.kind == "not_contains":
            needle = str(requirement.value).casefold()
            return ("passed", "") if needle not in text.casefold() else ("failed", f"Найден запрещённый фрагмент: {requirement.value}")
        if requirement.kind == "max_chars":
            limit = int(requirement.value)
            return ("passed", "") if len(text) <= limit else ("failed", f"{len(text)} > {limit}")
        if requirement.kind == "min_chars":
            limit = int(requirement.value)
            return ("passed", "") if len(text) >= limit else ("failed", f"{len(text)} < {limit}")
        if requirement.kind == "valid_json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return "failed", f"JSON error at line {exc.lineno}, column {exc.colno}"
            return "passed", ""
        raise ValueError(f"Unsupported requirement kind: {requirement.kind}")
