#!/usr/bin/env python3
"""Lease local image generation/edit jobs and execute configured backends."""
from __future__ import annotations

import argparse
import os
import socket
import threading
import time

from sqlalchemy import select

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import ImageEditRequest, ImageGeneration, utcnow
from app.services.image_editing import DiffusersImageEditBackend, LocalImageEditVision
from app.services.image_edit_runtime import execute_edit_generation
from app.services.image_runtime import DisabledImageBackend, LocalDiffusersBackend, MockImageBackend, execute_generation
from app.services.image_vision import LocalVisionQA
from app.services.image_worker_state import write_image_worker_heartbeat
from app.services.jobs import JobLeaseLostError, complete_job, fail_job, heartbeat_job, lease_next_job, start_job
from app.services.qwen_image_edit_backend import QwenImageEditBackend


def backend_for(name: str, *, model_path: str = "", model_name: str = ""):
    if name == "mock":
        return MockImageBackend()
    if name == "diffusers":
        return LocalDiffusersBackend(model_path, model_name)
    return DisabledImageBackend()


def edit_backend_for(settings):
    name = str(settings.image_edit_backend or "disabled").strip().lower()
    if name == "qwen-image-edit":
        return QwenImageEditBackend(
            model_path=settings.image_edit_model_path,
            identity_model_path=settings.image_edit_identity_model_path or settings.image_edit_model_path,
            require_cuda=True,
        )
    if name == "diffusers":
        return DiffusersImageEditBackend(
            inpaint_model_path=settings.image_edit_model_path,
            identity_model_path=settings.image_edit_identity_model_path,
        )
    return DiffusersImageEditBackend(inpaint_model_path="", identity_model_path="")


def _vision_services(settings):
    url = str(settings.image_vision_qa_url or "").strip()
    if not url:
        return None, None
    common = {
        "trusted_internal_base_url": str(settings.llama_base_url or ""),
        "model_name": str(settings.llama_model_name or "local-vision"),
    }
    semantic = LocalVisionQA(url, settings.image_vision_qa_timeout_seconds, **common)
    editing = LocalImageEditVision(url, settings.image_vision_qa_timeout_seconds, **common)
    return semantic, editing


def _write_worker_heartbeat(settings, worker_id: str, *, status: str = "stable", message: str = "Image worker is alive") -> None:
    try:
        with SessionLocal() as db:
            write_image_worker_heartbeat(
                db,
                status=status,
                message=message,
                details={
                    "worker_id": worker_id,
                    "generation_backend": str(settings.image_backend),
                    "edit_backend": str(settings.image_edit_backend),
                    "vision_qa_configured": bool(str(settings.image_vision_qa_url or "").strip()),
                    "vision_model": str(settings.llama_model_name or ""),
                },
            )
            db.commit()
    except Exception:
        # Heartbeat visibility must never steal/abort a durable image job.
        return


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
            continue


def _mark_terminal_failure(db, generation_id: str | None, *, job_kind: str, error: str) -> None:
    if not generation_id:
        return
    generation = db.get(ImageGeneration, generation_id)
    if generation is not None:
        generation.status = "failed"
        generation.qa_status = "failed"
        generation.error_message = error[:2000]
        generation.finished_at = utcnow()
    if job_kind == "image.edit":
        edit = db.scalar(select(ImageEditRequest).where(ImageEditRequest.generation_id == generation_id))
        if edit is not None:
            edit.status = "failed"
            edit.error_message = error[:2000]
            edit.qa_summary = {**(edit.qa_summary or {}), "passed": False, "worker_failure": True, "reason": error[:500]}
            edit.updated_at = utcnow()


def run(*, persistent: bool = False) -> None:
    settings = get_settings()
    worker_id = f"image-{socket.gethostname()}-{os.getpid()}"
    generation_backend = backend_for(settings.image_backend, model_path=settings.image_model_path, model_name=settings.image_model_name)
    semantic_qa, edit_vision = _vision_services(settings)
    edit_backend = edit_backend_for(settings)
    idle_since = time.monotonic()
    next_worker_heartbeat = 0.0

    while True:
        now_mono = time.monotonic()
        if now_mono >= next_worker_heartbeat:
            _write_worker_heartbeat(settings, worker_id)
            next_worker_heartbeat = now_mono + 30.0

        with SessionLocal() as db:
            job = lease_next_job(
                db,
                worker_id=worker_id,
                lease_seconds=settings.job_lease_seconds,
                kinds={"image.generate", "image.edit"},
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
                        result_payload={"skipped": True, "job_kind": job.kind},
                    )
                    db.commit()
                    continue

                if job.kind == "image.edit":
                    result_payload = execute_edit_generation(
                        db,
                        generation,
                        backend=edit_backend,
                        storage_root=settings.image_storage_path,
                        vision=edit_vision,
                        qa_max_repairs=settings.image_edit_qa_max_repairs,
                        preview_max_side=settings.image_preview_max_side,
                        mask_padding_ratio=settings.image_edit_mask_padding_ratio,
                        mask_feather_px=settings.image_edit_mask_feather_px,
                        max_local_mask_ratio=settings.image_edit_max_local_mask_ratio,
                        min_plan_confidence=settings.image_edit_min_plan_confidence,
                        require_vision_qa=settings.image_edit_require_vision_qa,
                    )
                else:
                    execute_generation(
                        db,
                        generation,
                        backend=generation_backend,
                        storage_root=settings.image_storage_path,
                        max_perceptual_error=settings.image_perceptual_error_max,
                        preview_max_side=settings.image_preview_max_side,
                        max_repairs=settings.image_qa_max_repairs,
                        semantic_qa=semantic_qa,
                    )
                    result_payload = {
                        "delivered": generation.status == "ready" and generation.qa_status == "passed",
                        "generation_id": generation.id,
                        "blob_id": generation.blob_id,
                    }

                if heartbeat_lost.is_set():
                    db.rollback()
                    raise JobLeaseLostError("Image job lease was lost during execution")
                complete_job(
                    db,
                    job.id,
                    worker_id=worker_id,
                    lease_token=token,
                    result_payload={"job_kind": job.kind, **result_payload},
                )
                db.commit()
                _write_worker_heartbeat(settings, worker_id)
                next_worker_heartbeat = time.monotonic() + 30.0
            except Exception as exc:
                db.rollback()
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
                        if leased.status == "failed":
                            _mark_terminal_failure(failure_db, generation_id, job_kind=leased.kind, error=str(exc))
                        failure_db.commit()
                _write_worker_heartbeat(settings, worker_id, status="degraded", message=f"Image worker job failed: {type(exc).__name__}")
                next_worker_heartbeat = time.monotonic() + 10.0
            finally:
                heartbeat_stop.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 local image generation/edit worker")
    parser.add_argument("--persistent", action="store_true", help="Stay alive when the image queue is empty")
    args = parser.parse_args()
    run(persistent=args.persistent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
