from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ComplaintCase, RegressionCase, RegressionRun, ReleaseGateDecision

_ALLOWED_RESULTS = {"passed", "failed", "error"}
_CLASSIFICATION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("context_loss", ("забыл", "потерял контекст", "lost context", "forgot instruction")),
    ("hallucination_source", ("выдумал ссыл", "несуществующ", "hallucinat", "fake url", "fabricated")),
    ("repeated_read", ("повторно чит", "снова чит", "reread", "read again")),
    ("false_completion", ("говорит готов", "сказал готов", "false completion", "says done", "claimed done")),
    ("scope_change", ("изменил лиш", "лишние файл", "unrelated file", "out of scope")),
    ("agent_loop", ("зацик", "бесконеч", "runaway", "loop")),
    ("budget_overrun", ("лимит", "budget", "слишком много cpu", "compute overrun")),
    ("update_regression", ("после обнов", "after update", "regression after")),
    ("document_visual", ("таблиц", "обрезан", "съех", "layout", "page broken", "docx")),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def classify_complaint(*, title: str, actual_behavior: str, component: str, category: str) -> tuple[str, str]:
    text = _norm(f"{title} {actual_behavior}")
    resolved_category = _norm(category) or "auto"
    if resolved_category == "auto":
        resolved_category = "other"
        for name, needles in _CLASSIFICATION_RULES:
            if any(needle in text for needle in needles):
                resolved_category = name
                break
    resolved_component = _norm(component) or "auto"
    if resolved_component == "auto":
        if resolved_category == "document_visual":
            resolved_component = "documents"
        elif resolved_category in {"agent_loop", "scope_change", "false_completion"}:
            resolved_component = "engineering"
        elif resolved_category in {"context_loss", "repeated_read"}:
            resolved_component = "context"
        elif resolved_category == "budget_overrun":
            resolved_component = "runtime"
        else:
            resolved_component = "assistant"
    return resolved_component[:80], resolved_category[:64]


def complaint_fingerprint(*, component: str, category: str, actual_behavior: str) -> str:
    payload = "\x1f".join((_norm(component), _norm(category), _norm(actual_behavior)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_complaint(
    db: Session,
    *,
    reporter_user_id: str | None,
    component: str,
    category: str,
    severity: str,
    title: str,
    actual_behavior: str,
    expected_behavior: str = "",
    project_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
    reproduction: dict | None = None,
    evidence: dict | None = None,
) -> ComplaintCase:
    component, category = classify_complaint(
        title=title, actual_behavior=actual_behavior, component=component, category=category
    )
    fingerprint = complaint_fingerprint(component=component, category=category, actual_behavior=actual_behavior)
    row = ComplaintCase(
        reporter_user_id=reporter_user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        request_id=request_id,
        component=component,
        category=category,
        severity=severity,
        title=title.strip(),
        expected_behavior=expected_behavior.strip(),
        actual_behavior=actual_behavior.strip(),
        reproduction=reproduction or {},
        evidence=evidence or {},
        fingerprint=fingerprint,
        status="new",
    )
    db.add(row)
    db.flush()
    return row


def confirm_complaint(db: Session, complaint: ComplaintCase, *, admin_user_id: str, reproduction: dict | None = None) -> RegressionCase:
    if complaint.status == "rejected":
        raise ValueError("Rejected complaint cannot be confirmed")
    now = _now()
    complaint.status = "confirmed"
    complaint.confirmed_by = admin_user_id
    complaint.confirmed_at = now
    if reproduction:
        complaint.reproduction = reproduction

    regression = db.scalar(select(RegressionCase).where(RegressionCase.fingerprint == complaint.fingerprint))
    if regression is None:
        regression = RegressionCase(
            source_complaint_id=complaint.id,
            fingerprint=complaint.fingerprint,
            component=complaint.component,
            category=complaint.category,
            severity=complaint.severity,
            title=complaint.title,
            spec={
                "version": 1,
                "runner": "complaint_contract",
                "component": complaint.component,
                "category": complaint.category,
                "input": complaint.reproduction,
                "expected_behavior": complaint.expected_behavior,
                "forbidden_behavior": complaint.actual_behavior,
                "evidence": complaint.evidence,
                "acceptance": {
                    "must_not_repeat_confirmed_failure": True,
                    "requires_recorded_result": True,
                },
            },
            confirmed_occurrences=1,
            release_blocking=False,
            last_confirmed_at=now,
        )
        db.add(regression)
        db.flush()
        return regression

    other_confirmed = int(
        db.scalar(
            select(func.count()).select_from(ComplaintCase).where(
                ComplaintCase.fingerprint == complaint.fingerprint,
                ComplaintCase.status == "confirmed",
                ComplaintCase.id != complaint.id,
            )
        ) or 0
    )
    regression.confirmed_occurrences = max(regression.confirmed_occurrences, other_confirmed + 1)
    regression.last_confirmed_at = now
    regression.release_blocking = regression.confirmed_occurrences >= 2
    regression.updated_at = now
    return regression


def reject_complaint(complaint: ComplaintCase) -> None:
    if complaint.status == "confirmed":
        raise ValueError("Confirmed complaint must be resolved, not rejected")
    complaint.status = "rejected"
    complaint.updated_at = _now()


def resolve_complaint(complaint: ComplaintCase) -> None:
    complaint.status = "resolved"
    complaint.resolved_at = _now()
    complaint.updated_at = complaint.resolved_at


def record_regression_run(
    db: Session,
    regression: RegressionCase,
    *,
    release_version: str,
    result: str,
    details: dict | None,
    executed_by: str | None,
) -> RegressionRun:
    if result not in _ALLOWED_RESULTS:
        raise ValueError("Unsupported regression result")
    row = RegressionRun(
        regression_case_id=regression.id,
        release_version=release_version,
        result=result,
        details=details or {},
        executed_by=executed_by,
    )
    db.add(row)
    now = _now()
    regression.latest_result = result
    regression.latest_release = release_version
    regression.latest_run_at = now
    if regression.confirmed_occurrences >= 2:
        regression.release_blocking = result != "passed"
    regression.updated_at = now
    db.flush()
    return row


def evaluate_release_gate(
    db: Session,
    *,
    release_version: str,
    channel: str = "stable",
    evaluated_by: str | None = None,
) -> ReleaseGateDecision:
    reasons: list[dict] = []
    blocker_ids: list[str] = []
    if channel == "stable":
        cases = list(db.scalars(select(RegressionCase).where(RegressionCase.status == "active")).all())
        for case in cases:
            if case.confirmed_occurrences < 2:
                continue
            passed_after_repeat = (
                case.latest_result == "passed"
                and case.latest_run_at is not None
                and case.latest_run_at >= case.last_confirmed_at
            )
            if not passed_after_repeat:
                blocker_ids.append(case.id)
                reasons.append({
                    "kind": "repeated_confirmed_defect",
                    "regression_case_id": case.id,
                    "fingerprint": case.fingerprint,
                    "confirmed_occurrences": case.confirmed_occurrences,
                    "latest_result": case.latest_result,
                    "latest_release": case.latest_release,
                })
    row = ReleaseGateDecision(
        release_version=release_version,
        channel=channel,
        decision="blocked" if blocker_ids else "approved",
        blocker_ids=blocker_ids,
        reasons=reasons,
        evaluated_by=evaluated_by,
    )
    db.add(row)
    db.flush()
    return row


def complaint_dict(row: ComplaintCase) -> dict:
    return {
        "id": row.id, "reporter_user_id": row.reporter_user_id,
        "project_id": row.project_id, "conversation_id": row.conversation_id,
        "request_id": row.request_id, "component": row.component,
        "category": row.category, "severity": row.severity, "title": row.title,
        "expected_behavior": row.expected_behavior, "actual_behavior": row.actual_behavior,
        "reproduction": row.reproduction, "evidence": row.evidence,
        "fingerprint": row.fingerprint, "status": row.status,
        "confirmed_at": row.confirmed_at, "resolved_at": row.resolved_at,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def regression_dict(row: RegressionCase) -> dict:
    return {
        "id": row.id, "source_complaint_id": row.source_complaint_id,
        "fingerprint": row.fingerprint, "component": row.component,
        "category": row.category, "severity": row.severity, "title": row.title,
        "spec": row.spec, "status": row.status,
        "confirmed_occurrences": row.confirmed_occurrences,
        "release_blocking": row.release_blocking,
        "latest_result": row.latest_result, "latest_release": row.latest_release,
        "latest_run_at": row.latest_run_at, "last_confirmed_at": row.last_confirmed_at,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }
