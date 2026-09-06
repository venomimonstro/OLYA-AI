from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BetaSnapshot
from app.services.adaptive_capacity import active_wave, evaluate_wave
from app.services.beta import build_beta_snapshot, calculate_beta_metrics
from app.services.capacity import read_capacity_report

logger = logging.getLogger(__name__)


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run_beta_operations_tick(settings) -> dict:
    """Idempotent closed-beta maintenance tick.

    The tick is intentionally database-only: it never runs inference. It creates
    at most one snapshot per configured interval and re-evaluates the active
    admission wave against the newest persisted telemetry/capacity report.
    """
    cohort = str(getattr(settings, "beta_operations_cohort", "closed-beta-1"))
    window_days = max(1, min(180, int(getattr(settings, "beta_operations_window_days", 30))))
    snapshot_hours = max(1.0, float(getattr(settings, "beta_snapshot_interval_hours", 24.0)))
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        latest = db.scalar(
            select(BetaSnapshot)
            .where(BetaSnapshot.cohort == cohort)
            .order_by(BetaSnapshot.created_at.desc())
            .limit(1)
        )
        latest_at = _aware(latest.created_at) if latest is not None else None
        created_snapshot = None
        if latest_at is None or now - latest_at >= timedelta(hours=snapshot_hours):
            created_snapshot = build_beta_snapshot(db, cohort=cohort, window_days=window_days, now=now)

        wave = active_wave(db, cohort)
        decision = None
        if wave is not None:
            metrics = calculate_beta_metrics(db, cohort=cohort, window_days=window_days, now=now)
            decision = evaluate_wave(
                wave,
                current=metrics,
                capacity_report=read_capacity_report(settings, now=now),
                settings=settings,
                persist=True,
            )
        db.commit()
        return {
            "cohort": cohort,
            "snapshot_created": created_snapshot is not None,
            "snapshot_id": created_snapshot.id if created_snapshot is not None else None,
            "wave_id": wave.id if wave is not None else None,
            "wave_decision": decision,
            "checked_at": now.isoformat(),
        }


async def beta_operations_loop(settings) -> None:
    interval = max(300.0, float(getattr(settings, "beta_operations_check_interval_seconds", 3600.0)))
    # Run once shortly after startup so a stale wave is not left open for an
    # entire interval following a deploy/restart.
    await asyncio.sleep(min(30.0, interval))
    while True:
        try:
            result = await asyncio.to_thread(run_beta_operations_tick, settings)
            if result.get("wave_decision", {}).get("status") in {"paused_for_regression", "paused_for_budget"}:
                logger.warning("X1 beta admission automatically paused: %s", result["wave_decision"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("X1 beta operations tick failed")
        await asyncio.sleep(interval)
