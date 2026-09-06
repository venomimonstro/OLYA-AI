#!/usr/bin/env python3
"""Lease image.generate jobs and execute the configured local backend."""
from __future__ import annotations

import argparse
import os
import socket
import threading
import time

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import ImageGeneration, utcnow
from app.services.image_runtime import DisabledImageBackend, LocalDiffusersBackend, MockImageBackend, execute_generation
from app.services.image_vision import LocalVisionQA
from app.services.jobs import JobLeaseLostError, complete_job, fail_job, heartbeat_job, lease_next_job, start_job


def backend_for(name: str, *, model_path: str = "", model_name: str = ""):
    if name == "mock":
        return MockImageBackend()
    if name == "diffusers":
        return LocalDiffusersBackend(model_path, model_name)
    return DisabledImageBackend()


def _lease_heartbeat(job_id: str, worker_id: str, token: str, lease_seconds: int, stop: threading.Event, lost: threading.Event) -> None:
    interval = max(5.0, min(30.0, float(lease_seconds) / 3.0))
    while not stop.wait(interval):
        try:
            with SessionLocal() as db:
                heartbeat_job(db, job_id, worker_id=worker_id, lease_token=token, lease_seconds=lease_seconds)
                db.commit()
        except JobLeaseLostError:
            lost.set()
            return
        except Exception:
            # A transient DB failure is retried on the next short interval. If the
            # lease genuinely expires, the next heartbeat marks it lost.
            continue


def run(*, persistent: bool = False) -> None:
    settings = get_settings()
    worker_id = f"image-{socket.gethostname()}-{os.getpid()}"
    backend = backend_for(settings.image_backend, model_path=settings.image_model_path, model_name=settings.image_model_name)
    semantic_qa = (
        LocalVisionQA(settings.image_vision_qa_url, settings.image_vision_qa_timeout_seconds)
        if settings.image_vision_qa_url
        else None
    )
    idle_since = time.monotonic()

    while True:
        with SessionLocal() as db:
            job = lease_next_job(
                db,
                worker_id=worker_id,
                lease_seconds=settings.job_lease_seconds,
                kinds={"image.generate"},
            )
            if job is None:
                db.rollback()
                if not persistent and time.monotonic() - idle_since >= settings.image_worker_idle_exit_seconds:
                    return
                time.sleep(settings.job_poll_seconds)
                continue

            idle_since = time.monotonic()
            token = job.lease_token or ""
            heartbeat_stop = threading.Event()
            heartbeat_lost = threading.Event()
            heartbeat_thread: threading.Thread | None = None
            try:
                start_job(db, job, worker_id=worker_id, lease_token=token)
                db.commit()
                # Commit the running lease before expensive model work, then keep
                # it alive from an independent Session so the generation session
                # is never shared across threads.
                heartbeat_thread = threading.Thread(
                    target=_lease_heartbeat,
                    args=(job.id, worker_id, token, settings.job_lease_seconds, heartbeat_stop, heartbeat_lost),
                    daemon=True,
                    name=f"x1-image-heartbeat-{job.id[:8]}",
                )
                heartbeat_thread.start()

                generation = db.get(ImageGeneration, (job.payload or {}).get("generation_id"))
                if generation is None or generation.status == "cancelled":
                    if heartbeat_lost.is_set():
                        raise JobLeaseLostError("Image job lease was lost")
                    complete_job(
                        db,
                        job.id,
                        worker_id=worker_id,
                        lease_token=token,
                        result_payload={"skipped": True},
                    )
                    db.commit()
                    continue

                execute_generation(
                    db,
                    generation,
                    backend=backend,
                    storage_root=settings.image_storage_path,
                    max_perceptual_error=settings.image_perceptual_error_max,
                    preview_max_side=settings.image_preview_max_side,
                    max_repairs=settings.image_qa_max_repairs,
                    semantic_qa=semantic_qa,
                )
                if heartbeat_lost.is_set():
                    db.rollback()
                    raise JobLeaseLostError("Image job lease was lost during generation")
                complete_job(
                    db,
                    job.id,
                    worker_id=worker_id,
                    lease_token=token,
                    result_payload={"generation_id": generation.id, "blob_id": generation.blob_id},
                )
                db.commit()
            except Exception as exc:  # durable worker boundary
                db.rollback()
                # Lease loss is not a worker crash: another worker may already own
                # and be processing this durable job. Never mutate/requeue it using
                # a stale token; simply abandon this result and continue polling.
                if isinstance(exc, JobLeaseLostError) or heartbeat_lost.is_set():
                    continue
                with SessionLocal() as failure_db:
                    leased = failure_db.get(type(job), job.id)
                    if leased and leased.lease_token == token:
                        generation_id = (leased.payload or {}).get("generation_id")
                        try:
                            fail_job(
                                failure_db,
                                job.id,
                                worker_id=worker_id,
                                lease_token=token,
                                error_message=str(exc),
                            )
                        except JobLeaseLostError:
                            failure_db.rollback()
                            continue
                        if leased.status == "failed" and generation_id:
                            failed_generation = failure_db.get(ImageGeneration, generation_id)
                            if failed_generation is not None:
                                failed_generation.status = "failed"
                                failed_generation.error_message = str(exc)[:2000]
                                failed_generation.finished_at = utcnow()
                        failure_db.commit()
            finally:
                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 local image generation worker")
    parser.add_argument("--persistent", action="store_true", help="Stay alive when the image queue is empty")
    args = parser.parse_args()
    run(persistent=args.persistent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
