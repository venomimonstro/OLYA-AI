from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models_sprint31 import SystemCheckpoint, SystemHealthSnapshot
from app.services.reliability import collect_system_health as _base_collect
from app.services.reliability import latest_checkpoints as _base_points

STABLE = "stable"
DEGRADED = "degraded"
CRITICAL = "failed"
UNKNOWN = "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _production(settings) -> bool:
    return str(getattr(settings, "env", "development")).lower() in {"production", "prod", "stable"}


def _check(
    key: str,
    subsystem: str,
    status: str,
    message: str,
    *,
    critical: bool = False,
    dependency: str = "",
    details: dict | None = None,
    action: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "subsystem": subsystem,
        "status": status,
        "message": message,
        "critical": critical,
        "dependency": dependency,
        "latency_ms": 0,
        "severity": "critical" if status == CRITICAL and critical else "warning" if status != STABLE else "info",
        "details": {**(details or {}), "recommended_action": action},
    }


def _migration_check(db: Session, settings) -> dict[str, Any]:
    try:
        tables = set(inspect(db.get_bind()).get_table_names())
        if "alembic_version" not in tables:
            prod = _production(settings)
            return _check(
                "core.migrations",
                "core",
                CRITICAL if prod else STABLE,
                "Alembic version table is missing" if prod else "Development schema is not Alembic-stamped",
                critical=prod,
                dependency="core.database",
                details={"mode": "production" if prod else "development-create_all"},
                action="Run alembic upgrade head before accepting traffic." if prod else "",
            )
        current = str(db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1")) or "")
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[2]
        heads = sorted(ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_heads())
        if current not in heads:
            return _check(
                "core.migrations",
                "core",
                CRITICAL,
                "Database migration head is stale",
                critical=True,
                dependency="core.database",
                details={"current": current, "expected_heads": heads},
                action="Run alembic upgrade head and repeat readiness check.",
            )
        return _check(
            "core.migrations",
            "core",
            STABLE,
            "Database migration head is current",
            critical=True,
            dependency="core.database",
            details={"current": current},
        )
    except Exception as exc:
        prod = _production(settings)
        return _check(
            "core.migrations",
            "core",
            CRITICAL if prod else UNKNOWN,
            f"Migration state could not be verified: {type(exc).__name__}",
            critical=prod,
            dependency="core.database",
            details={"error_type": type(exc).__name__},
            action="Run x1 doctor and migration verification.",
        )


def _route_contract(app) -> dict[str, Any]:
    """Verify representative public surfaces using their real router prefixes."""
    paths = {str(getattr(route, "path", "")) for route in app.routes}
    prefixes = {
        "auth": ("/v1/auth",),
        "chat": ("/v1/chat",),
        "projects": ("/v1/projects",),
        "documents": ("/v1/documents",),
        "images": ("/v1/images",),
        "research": ("/v1/research",),
        "development": ("/v1/development-plans", "/v1/development"),
        "engineering": ("/v1/engineering-runs", "/v1/engineering"),
        "sandbox": ("/v1/project-sandboxes", "/v1/sandbox"),
        "git": ("/v1/git",),
        "commerce": ("/v1/commerce",),
        "external_api": ("/v1/api",),
        "complaints": ("/v1/feedback/complaints", "/v1/complaints"),
        "beta": ("/v1/admin/beta",),
        "reliability": ("/v1/admin/reliability",),
        "operations": ("/v1/admin/operations",),
    }
    missing = sorted(
        name
        for name, accepted_prefixes in prefixes.items()
        if not any(path.startswith(prefix) for path in paths for prefix in accepted_prefixes)
    )
    if missing:
        return _check(
            "product.route_contract",
            "api",
            CRITICAL,
            "Required product API surfaces are not registered",
            critical=True,
            details={"missing_features": missing, "route_count": len(paths)},
            action="Restore/register missing routers before Stable release.",
        )
    return _check(
        "product.route_contract",
        "api",
        STABLE,
        "Required product API surfaces are registered",
        critical=True,
        details={"features": sorted(prefixes), "route_count": len(paths)},
    )


def _verify_manifest(folder: Path) -> list[str]:
    manifest = folder / "SHA256SUMS"
    if not manifest.exists():
        return ["SHA256SUMS missing"]
    errors: list[str] = []
    for line in manifest.read_text("utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        expected, name = parts
        target = folder / name.lstrip("*")
        if not target.is_file():
            errors.append(f"missing:{name}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            errors.append(f"checksum:{name}")
    return errors


def _backup_check(settings, *, deep: bool) -> dict[str, Any]:
    root = Path(getattr(settings, "backup_storage_path", "./backups")).expanduser().resolve()
    prod = _production(settings)
    if not root.exists():
        return _check(
            "ops.backup",
            "operations",
            DEGRADED if prod else STABLE,
            "No backup directory found",
            details={"path": str(root)},
            action="Create and verify a backup." if prod else "",
        )
    dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and ".partial." not in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not dirs:
        return _check(
            "ops.backup",
            "operations",
            DEGRADED if prod else STABLE,
            "No backup snapshots found",
            details={"path": str(root)},
            action="Create a backup and run a restore drill." if prod else "",
        )
    latest = dirs[0]
    age_hours = max(0.0, (_now().timestamp() - latest.stat().st_mtime) / 3600)
    if deep:
        errors = _verify_manifest(latest)
        if errors:
            return _check(
                "ops.backup",
                "operations",
                CRITICAL,
                "Latest backup checksum verification failed",
                critical=True,
                details={"path": str(latest), "errors": errors, "age_hours": round(age_hours, 2)},
                action="Create a new verified backup immediately.",
            )
    stale = age_hours > float(getattr(settings, "health_backup_max_age_hours", 36.0))
    return _check(
        "ops.backup",
        "operations",
        DEGRADED if stale else STABLE,
        "Latest backup is stale" if stale else "Latest backup is recent",
        details={"path": str(latest), "age_hours": round(age_hours, 2), "checksum_verified": deep},
        action="Create a fresh backup and verify restore." if stale else "",
    )


def _read_report(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None, "invalid"
    return (data, "") if isinstance(data, dict) else (None, "invalid")


def _report_age_hours(path: Path, payload: dict[str, Any]) -> float:
    raw = str(payload.get("finished_at") or "").strip()
    if raw:
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return max(0.0, (_now() - value).total_seconds() / 3600.0)
        except ValueError:
            pass
    return max(0.0, (_now().timestamp() - path.stat().st_mtime) / 3600.0)


def _release_gate_check(settings, *, app_version: str) -> dict[str, Any]:
    path = Path(getattr(settings, "release_gate_report_path", "./backups/release-gate-latest.json")).expanduser().resolve()
    payload, error = _read_report(path)
    prod = _production(settings)
    if payload is None:
        return _check(
            "ops.release_gate",
            "release",
            DEGRADED if prod else STABLE,
            "Production release gate has not been recorded" if error == "missing" else "Release gate report is invalid",
            details={"path": str(path), "report_error": error},
            action="Run python scripts/release_gate.py --runtime --live-inference on the target node." if prod else "",
        )
    age = _report_age_hours(path, payload)
    max_age = float(getattr(settings, "release_gate_max_age_hours", 24.0))
    reasons: list[str] = []
    if payload.get("status") != "passed":
        reasons.append("gate_failed")
    if str(payload.get("version") or "") != str(app_version):
        reasons.append("version_mismatch")
    if prod and payload.get("mode") != "runtime":
        reasons.append("runtime_gate_not_run")
    if prod and not bool(payload.get("live_inference_requested")):
        reasons.append("live_inference_not_run")
    if age > max_age:
        reasons.append("stale")
    stable = not reasons
    return _check(
        "ops.release_gate",
        "release",
        STABLE if stable else DEGRADED,
        "Production release gate passed" if stable else "Production release gate is not current",
        details={
            "path": str(path),
            "age_hours": round(age, 2),
            "max_age_hours": max_age,
            "version": payload.get("version"),
            "app_version": app_version,
            "mode": payload.get("mode"),
            "live_inference_requested": bool(payload.get("live_inference_requested")),
            "failed_required_checks": payload.get("failed_required_checks") or [],
            "reasons": reasons,
        },
        action="Re-run the full release gate on the deployed revision." if reasons else "",
    )


def _restore_drill_check(settings) -> dict[str, Any]:
    path = Path(getattr(settings, "restore_drill_report_path", "./backups/restore-drill-latest.json")).expanduser().resolve()
    payload, error = _read_report(path)
    prod = _production(settings)
    if payload is None:
        return _check(
            "ops.restore_drill",
            "release",
            DEGRADED if prod else STABLE,
            "Restore drill has not been recorded" if error == "missing" else "Restore drill report is invalid",
            details={"path": str(path), "report_error": error},
            action="Run bash scripts/restore_drill.sh against a fresh backup." if prod else "",
        )
    age = _report_age_hours(path, payload)
    max_age = float(getattr(settings, "restore_drill_max_age_hours", 168.0))
    reasons: list[str] = []
    if payload.get("status") != "passed":
        reasons.append("drill_failed")
    if age > max_age:
        reasons.append("stale")
    stable = not reasons
    return _check(
        "ops.restore_drill",
        "release",
        STABLE if stable else DEGRADED,
        "Verified restore drill is current" if stable else "Restore drill is not current",
        details={
            "path": str(path),
            "age_hours": round(age, 2),
            "max_age_hours": max_age,
            "backup": payload.get("backup"),
            "alembic_version": payload.get("alembic_version"),
            "restored_table_count": payload.get("restored_table_count"),
            "reasons": reasons,
        },
        action="Run a fresh non-destructive restore drill." if reasons else "",
    )


def _root_causes(checks: list[dict[str, Any]]) -> list[str]:
    by_key = {item["key"]: item for item in checks}
    roots: set[str] = set()
    for item in checks:
        if item.get("status") == STABLE:
            item["root_cause"] = ""
            continue
        key = item["key"]
        seen: set[str] = set()
        while key not in seen:
            seen.add(key)
            current = by_key.get(key)
            dependency = current.get("dependency", "") if current else ""
            parent = by_key.get(dependency) if dependency else None
            if not parent or parent.get("status") == STABLE:
                break
            key = dependency
        item["root_cause"] = key
        roots.add(key)
    return sorted(roots)


def _persist_checkpoint(db: Session, item: dict[str, Any]) -> None:
    row = db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key == item["key"]))
    now = _now()
    if row is None:
        row = SystemCheckpoint(key=item["key"], subsystem=item["subsystem"])
        db.add(row)
        db.flush()
    previous = int(row.consecutive_failures or 0)
    row.subsystem = item["subsystem"]
    row.dependency = item.get("dependency", "")
    row.status = item["status"]
    row.severity = item.get("severity", "info")
    row.critical = bool(item.get("critical"))
    row.latency_ms = int(item.get("latency_ms", 0))
    row.message = str(item.get("message", ""))[:500]
    row.details = {**(item.get("details") or {}), "root_cause": item.get("root_cause", "")}
    row.last_checked_at = now
    if item["status"] == STABLE:
        row.consecutive_failures = 0
        row.last_ok_at = now
    else:
        row.consecutive_failures = previous + 1


async def collect_system_health(app, db: Session, *, persist: bool = True, deep: bool = False) -> dict[str, Any]:
    base = await _base_collect(app, db, persist=False, deep=deep)
    checks = list(base.get("checks") or [])
    checks.extend(
        [
            _migration_check(db, app.state.settings),
            _route_contract(app),
            _backup_check(app.state.settings, deep=deep),
            _release_gate_check(app.state.settings, app_version=str(getattr(app, "version", "unknown"))),
            _restore_drill_check(app.state.settings),
        ]
    )
    roots = _root_causes(checks)
    stable = sum(item.get("status") == STABLE for item in checks)
    degraded = sum(item.get("status") == DEGRADED for item in checks)
    failed = sum(item.get("status") == CRITICAL for item in checks)
    unknown = sum(item.get("status") == UNKNOWN for item in checks)
    critical_failed = sum(item.get("status") == CRITICAL and item.get("critical") for item in checks)
    status = CRITICAL if critical_failed else DEGRADED if failed or degraded else UNKNOWN if unknown else STABLE
    score = max(0, min(100, round((stable * 100 + degraded * 55 + unknown * 30) / max(1, len(checks)))))
    result = {
        "status": status,
        "score": score,
        "stable": stable,
        "degraded": degraded,
        "failed": failed,
        "critical": failed,
        "unknown": unknown,
        "critical_failed": critical_failed,
        "root_causes": roots,
        "checks": checks,
        "checked_at": _now(),
    }
    if persist:
        for item in checks:
            _persist_checkpoint(db, item)
        snapshot = SystemHealthSnapshot(
            overall_status=status,
            score=score,
            stable_count=stable,
            degraded_count=degraded + unknown,
            failed_count=failed,
            critical_failed_count=critical_failed,
            checks=checks,
        )
        db.add(snapshot)
        db.flush()
        result["snapshot_id"] = snapshot.id
    return result


def latest_checkpoints(db: Session, *, stale_after_seconds: int = 300) -> list[dict[str, Any]]:
    rows = _base_points(db)
    now = _now()
    result: list[dict[str, Any]] = []
    for item in rows:
        checked = _aware(item.get("last_checked_at"))
        age = (now - checked).total_seconds() if checked else float("inf")
        stale = age > max(30, stale_after_seconds)
        details = dict(item.get("details") or {})
        out = dict(item)
        out["stored_status"] = item.get("status")
        out["status"] = UNKNOWN if stale else item.get("status", UNKNOWN)
        out["stale"] = stale
        out["age_seconds"] = None if age == float("inf") else round(age, 1)
        out["root_cause"] = details.get("root_cause", "")
        out["recommended_action"] = "Refresh system health checks." if stale else details.get("recommended_action", "")
        out["message"] = "Checkpoint is stale" if stale else out.get("message", "")
        result.append(out)
    return result
