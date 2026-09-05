from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.models import (
    AnswerAudit,
    BackgroundJob,
    ComplaintCase,
    DocumentQAEvent,
    EngineeringExecution,
    FrustrationEvent,
    GitOperation,
    ProjectSandboxRun,
    RegressionCase,
    ResearchRun,
    SystemCheckpoint,
    SystemHealthSnapshot,
    UsageEvent,
)


@dataclass(slots=True)
class CheckResult:
    key: str
    subsystem: str
    status: str
    message: str
    critical: bool = False
    severity: str = "info"
    dependency: str = ""
    latency_ms: int = 0
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["details"] = self.details or {}
        return data


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _result(key: str, subsystem: str, status: str, message: str, *, critical: bool = False,
            dependency: str = "", latency_ms: int = 0, details: dict | None = None) -> CheckResult:
    severity = "critical" if status == "failed" and critical else "warning" if status != "stable" else "info"
    return CheckResult(key=key, subsystem=subsystem, status=status, message=message, critical=critical,
                       severity=severity, dependency=dependency, latency_ms=latency_ms, details=details or {})


def _safe_count(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def _database_probe(db: Session) -> CheckResult:
    started = perf_counter()
    try:
        db.execute(text("SELECT 1"))
        latency = int((perf_counter() - started) * 1000)
        return _result("core.database", "core", "stable", "Database query succeeded", critical=True,
                       latency_ms=latency, details={"latency_ms": latency})
    except Exception as exc:
        db.rollback()
        return _result("core.database", "core", "failed", f"Database query failed: {type(exc).__name__}",
                       critical=True, details={"error_type": type(exc).__name__})


def _schema_probe(db: Session) -> CheckResult:
    required = {
        "users", "auth_sessions", "projects", "tasks", "usage_events", "background_jobs",
        "answer_audits", "research_sources", "frustration_events", "document_artifacts",
        "engineering_executions", "project_sandbox_runs", "git_operations",
        "complaint_cases", "regression_cases", "release_gate_decisions",
        "system_checkpoints", "system_health_snapshots",
    }
    try:
        tables = set(inspect(db.get_bind()).get_table_names())
    except Exception as exc:
        return _result("core.schema", "core", "failed", f"Schema inspection failed: {type(exc).__name__}",
                       critical=True)
    missing = sorted(required - tables)
    if missing:
        return _result("core.schema", "core", "failed", "Database schema is incomplete", critical=True,
                       dependency="core.database", details={"missing_tables": missing})
    return _result("core.schema", "core", "stable", "Critical schema tables are present", critical=True,
                   dependency="core.database", details={"required_tables": len(required)})


def _configuration_probe(settings) -> CheckResult:
    unsafe: list[str] = []
    if settings.env.lower() in {"production", "prod", "stable"}:
        if not settings.admin_bootstrap_token or settings.admin_bootstrap_token == "change-me":
            unsafe.append("admin_bootstrap_token")
        if not settings.project_runtime_secret_key or settings.project_runtime_secret_key == "change-me-runtime-secret":
            unsafe.append("project_runtime_secret_key")
    if unsafe:
        return _result("core.configuration", "core", "failed", "Unsafe default production secrets detected",
                       critical=True, details={"unsafe_settings": unsafe})
    defaults = []
    if settings.admin_bootstrap_token == "change-me":
        defaults.append("admin_bootstrap_token")
    if settings.project_runtime_secret_key == "change-me-runtime-secret":
        defaults.append("project_runtime_secret_key")
    if defaults:
        return _result("core.configuration", "core", "degraded", "Development defaults are still configured",
                       details={"defaults": defaults, "env": settings.env})
    return _result("core.configuration", "core", "stable", "Security-sensitive configuration is non-default")


def _storage_probe(settings, *, deep: bool) -> CheckResult:
    paths = [settings.file_storage_path, settings.document_storage_path, settings.code_workspace_storage_path,
             settings.project_runtime_storage_path]
    details: dict[str, Any] = {"paths": []}
    failed: list[str] = []
    degraded: list[str] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            stat = os.statvfs(path)
            free_bytes = stat.f_bavail * stat.f_frsize
            total_bytes = stat.f_blocks * stat.f_frsize
            free_pct = (free_bytes / total_bytes * 100.0) if total_bytes else 0.0
            writable = os.access(path, os.W_OK)
            if deep and writable:
                fd, probe_path = tempfile.mkstemp(prefix=".x1-health-", dir=path)
                try:
                    os.write(fd, b"x1")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                    os.unlink(probe_path)
            if not writable:
                failed.append(str(path))
            elif free_bytes < 512 * 1024 * 1024 or free_pct < 3.0:
                degraded.append(str(path))
            details["paths"].append({"path": str(path), "writable": writable, "free_bytes": int(free_bytes), "free_percent": round(free_pct, 2)})
        except Exception as exc:
            failed.append(str(path))
            details["paths"].append({"path": str(path), "error": type(exc).__name__})
    if failed:
        return _result("core.storage", "storage", "failed", "One or more storage paths are not usable", critical=True,
                       details={**details, "failed_paths": failed})
    if degraded:
        return _result("core.storage", "storage", "degraded", "Storage free space is low", critical=True,
                       details={**details, "low_space_paths": degraded})
    return _result("core.storage", "storage", "stable", "Storage paths are writable with safe free space", critical=True, details=details)


def _queue_probe(db: Session, app) -> CheckResult:
    now = _now()
    queued = _safe_count(db, select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status == "queued"))
    stale = _safe_count(db, select(func.count()).select_from(BackgroundJob).where(
        BackgroundJob.status == "running", BackgroundJob.lease_expires_at.is_not(None), BackgroundJob.lease_expires_at < now
    ))
    waiting = int(getattr(app.state.governor, "waiting", 0))
    max_queue = max(1, int(getattr(app.state.governor, "max_queue", 1)))
    ratio = waiting / max_queue
    details = {"inference_waiting": waiting, "inference_max_queue": max_queue, "queued_jobs": queued, "stale_job_leases": stale}
    if stale > 0 or ratio >= 1.0:
        return _result("runtime.queue", "runtime", "failed", "Queue or job lease health is unsafe", critical=True, details=details)
    if ratio >= 0.7 or queued > max(20, max_queue * 2):
        return _result("runtime.queue", "runtime", "degraded", "Queue pressure is elevated", critical=True, details=details)
    return _result("runtime.queue", "runtime", "stable", "Queue and job leases are healthy", critical=True, details=details)


def _recent_signal_probe(db: Session, *, minutes: int = 60) -> list[CheckResult]:
    since = _now() - timedelta(minutes=minutes)
    total = _safe_count(db, select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= since))
    failed = _safe_count(db, select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= since, UsageEvent.success.is_(False)))
    failure_ratio = failed / total if total else 0.0
    if total and failure_ratio >= 0.20:
        runtime = _result("link.inference_persistence", "chat", "failed", "Recent request failure rate is high", critical=True,
                          details={"requests": total, "failed": failed, "failure_ratio": round(failure_ratio, 4)})
    elif total and failure_ratio >= 0.05:
        runtime = _result("link.inference_persistence", "chat", "degraded", "Recent request failure rate is elevated", critical=True,
                          details={"requests": total, "failed": failed, "failure_ratio": round(failure_ratio, 4)})
    else:
        runtime = _result("link.inference_persistence", "chat", "stable", "Inference-to-persistence telemetry is healthy",
                          critical=True, details={"requests": total, "failed": failed, "failure_ratio": round(failure_ratio, 4)})

    inflation = _safe_count(db, select(func.count()).select_from(FrustrationEvent).where(
        FrustrationEvent.created_at >= since, FrustrationEvent.kind == "context_inflation", FrustrationEvent.resolved.is_(False)
    ))
    context = _result("link.context_inference", "context", "degraded" if inflation >= 3 else "stable",
                      "Repeated context inflation detected" if inflation >= 3 else "Context-to-inference link is healthy",
                      details={"open_context_inflation_events": inflation})

    quality_total = _safe_count(db, select(func.count()).select_from(AnswerAudit).where(AnswerAudit.created_at >= since))
    quality_failed = _safe_count(db, select(func.count()).select_from(AnswerAudit).where(
        AnswerAudit.created_at >= since, AnswerAudit.status == "failed"
    ))
    qratio = quality_failed / quality_total if quality_total else 0.0
    quality = _result("link.answer_verification", "quality", "degraded" if quality_total and qratio >= 0.10 else "stable",
                      "Quality failure rate is elevated" if quality_total and qratio >= 0.10 else "Answer verification link is healthy",
                      details={"audits": quality_total, "failed": quality_failed, "failure_ratio": round(qratio, 4)})
    return [runtime, context, quality]


def _pipeline_probe(db: Session) -> list[CheckResult]:
    def failed_count(model, status_col, bad: tuple[str, ...]) -> int:
        since = _now() - timedelta(hours=24)
        return _safe_count(db, select(func.count()).select_from(model).where(model.created_at >= since, status_col.in_(bad)))

    doc_failed = failed_count(DocumentQAEvent, DocumentQAEvent.status, ("failed", "error"))
    research_failed = failed_count(ResearchRun, ResearchRun.status, ("failed", "error"))
    eng_failed = failed_count(EngineeringExecution, EngineeringExecution.status, ("failed", "error"))
    sandbox_failed = failed_count(ProjectSandboxRun, ProjectSandboxRun.status, ("failed", "error"))
    git_failed = failed_count(GitOperation, GitOperation.status, ("failed", "error"))
    return [
        _result("link.document_render", "documents", "degraded" if doc_failed >= 3 else "stable",
                "Repeated document QA failures" if doc_failed >= 3 else "Document QA/render link is healthy", details={"failures_24h": doc_failed}),
        _result("link.research_grounding", "research", "degraded" if research_failed >= 3 else "stable",
                "Repeated research pipeline failures" if research_failed >= 3 else "Research pipeline is healthy", details={"failures_24h": research_failed}),
        _result("link.engineering_sandbox", "engineering", "degraded" if eng_failed + sandbox_failed >= 3 else "stable",
                "Repeated engineering/sandbox failures" if eng_failed + sandbox_failed >= 3 else "Engineering-to-sandbox link is healthy",
                details={"engineering_failures_24h": eng_failed, "sandbox_failures_24h": sandbox_failed}),
        _result("link.git_runtime", "engineering", "degraded" if git_failed >= 3 else "stable",
                "Repeated Git operation failures" if git_failed >= 3 else "Git/runtime link is healthy", details={"git_failures_24h": git_failed}),
    ]


def _release_gate_probe(db: Session) -> CheckResult:
    blockers = list(db.scalars(select(RegressionCase).where(
        RegressionCase.status == "active", RegressionCase.confirmed_occurrences >= 2,
        RegressionCase.release_blocking.is_(True)
    )).all())
    open_critical = _safe_count(db, select(func.count()).select_from(ComplaintCase).where(
        ComplaintCase.status.in_(("new", "confirmed")), ComplaintCase.severity == "critical"
    ))
    status = "degraded" if blockers or open_critical else "stable"
    return _result("quality.release_gate", "quality", status,
                   "Stable release has unresolved blockers" if status == "degraded" else "No active Stable release blockers",
                   details={"regression_blockers": [x.id for x in blockers], "open_critical_complaints": open_critical})


def _persist_checkpoint(db: Session, check: CheckResult) -> SystemCheckpoint:
    row = db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key == check.key))
    now = _now()
    if row is None:
        row = SystemCheckpoint(key=check.key, subsystem=check.subsystem)
        db.add(row)
        db.flush()
    previous_failures = row.consecutive_failures
    row.subsystem = check.subsystem
    row.dependency = check.dependency
    row.status = check.status
    row.severity = check.severity
    row.critical = check.critical
    row.latency_ms = check.latency_ms
    row.message = check.message[:500]
    row.details = check.details or {}
    row.last_checked_at = now
    if check.status == "stable":
        row.consecutive_failures = 0
        row.last_ok_at = now
    else:
        row.consecutive_failures = previous_failures + 1
    return row


def _aggregate(checks: list[CheckResult]) -> dict[str, Any]:
    stable = sum(x.status == "stable" for x in checks)
    degraded = sum(x.status == "degraded" for x in checks)
    failed = sum(x.status == "failed" for x in checks)
    critical_failed = sum(x.status == "failed" and x.critical for x in checks)
    if critical_failed:
        overall = "failed"
    elif failed or degraded:
        overall = "degraded"
    else:
        overall = "stable"
    score = max(0, min(100, round((stable * 100 + degraded * 55) / max(1, len(checks)))))
    return {"status": overall, "score": score, "stable": stable, "degraded": degraded, "failed": failed,
            "critical_failed": critical_failed, "checks": [x.as_dict() for x in checks], "checked_at": _now()}


async def collect_system_health(app, db: Session, *, persist: bool = False, deep: bool = False) -> dict[str, Any]:
    checks: list[CheckResult] = []
    db_check = _database_probe(db)
    checks.append(db_check)
    if db_check.status == "failed":
        return _aggregate(checks)
    checks.append(_schema_probe(db))
    checks.append(_configuration_probe(app.state.settings))
    checks.append(_storage_probe(app.state.settings, deep=deep))

    started = perf_counter()
    try:
        inference_ok = bool(await app.state.llama.health())
    except Exception as exc:
        inference_ok = False
        inference_error = type(exc).__name__
    else:
        inference_error = ""
    inference_latency = int((perf_counter() - started) * 1000)
    checks.append(_result("core.inference", "inference", "stable" if inference_ok else "failed",
                          "Local inference is reachable" if inference_ok else "Local inference health check failed",
                          critical=True, latency_ms=inference_latency,
                          details={"latency_ms": inference_latency, "error_type": inference_error}))
    checks.append(_queue_probe(db, app))
    checks.extend(_recent_signal_probe(db))
    checks.extend(_pipeline_probe(db))
    checks.append(_release_gate_probe(db))
    result = _aggregate(checks)
    if persist:
        for check in checks:
            _persist_checkpoint(db, check)
        snap = SystemHealthSnapshot(
            overall_status=result["status"], score=result["score"], stable_count=result["stable"],
            degraded_count=result["degraded"], failed_count=result["failed"],
            critical_failed_count=result["critical_failed"], checks=result["checks"],
        )
        db.add(snap)
        db.commit()
        result["snapshot_id"] = snap.id
    return result


def latest_checkpoints(db: Session) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(SystemCheckpoint).order_by(SystemCheckpoint.critical.desc(), SystemCheckpoint.status, SystemCheckpoint.key)).all())
    return [{
        "key": x.key, "subsystem": x.subsystem, "dependency": x.dependency, "status": x.status,
        "severity": x.severity, "critical": x.critical, "latency_ms": x.latency_ms,
        "message": x.message, "details": x.details, "consecutive_failures": x.consecutive_failures,
        "last_ok_at": x.last_ok_at, "last_checked_at": x.last_checked_at,
    } for x in rows]
