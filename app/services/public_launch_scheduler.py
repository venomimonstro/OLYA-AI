from __future__ import annotations

import asyncio
import logging

from app.db import SessionLocal
from app.services.progressive_launch import active_rollout, evaluate_public_launch, freeze_rollout, rollback_rollout
from app.services.system_observability import collect_system_health

logger = logging.getLogger(__name__)


def _apply_safety_action(db, settings, evaluation) -> dict:
    rollout = active_rollout(db)
    action = "none"
    new_rollout_id = None
    if rollout is not None and evaluation.get("status") != "stable":
        freeze_rollout(rollout, evaluation=evaluation)
        action = "frozen"
        if bool(getattr(settings, "public_launch_auto_rollback", True)) and rollout.exposure_percent > 0:
            replacement = rollback_rollout(db, rollout, actor_id=None)
            new_rollout_id = replacement.id
            action = "rolled_back"
    return {"status":evaluation.get("status"),"blockers":evaluation.get("blockers") or [],"action":action,"rollout_id":rollout.id if rollout else None,"replacement_rollout_id":new_rollout_id}


def run_public_launch_tick(settings) -> dict:
    """Synchronous maintenance entry point for tests/manual use.

    The production async watchdog additionally refreshes Operational Checkpoints
    immediately before this launch evaluation.
    """
    with SessionLocal() as db:
        evaluation = evaluate_public_launch(db, settings, persist=True)
        result = _apply_safety_action(db, settings, evaluation)
        db.commit()
        return result


async def public_launch_watchdog_loop(app) -> None:
    settings = app.state.settings
    interval = max(60.0, float(getattr(settings, "public_launch_watchdog_interval_seconds", 300.0)))
    await asyncio.sleep(min(30.0, interval))
    while True:
        try:
            with SessionLocal() as db:
                # Keep the same checkpoint source used by /ready and Admin Health.
                await collect_system_health(app, db, persist=True, deep=False)
                evaluation = evaluate_public_launch(db, settings, persist=True)
                result = _apply_safety_action(db, settings, evaluation)
                db.commit()
            if result.get("action") in {"frozen", "rolled_back"}:
                logger.warning("X1 public rollout safety action: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("X1 public launch watchdog tick failed")
        await asyncio.sleep(interval)
