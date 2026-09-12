from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ImageGeneration, User
from app.services.image_capabilities import image_edit_capabilities
from app.services.image_references import total_user_image_storage_bytes
from app.services.image_worker_state import image_worker_snapshot
from app.services.quota import compute_seconds_used, get_or_create_quota
from app.services.safety import active_restriction
from app.services.sandbox import sandbox_capabilities

REGISTRY_VERSION = "x1-capabilities-v1"

CAPABILITY_IDS = (
    "chat",
    "projects",
    "files",
    "documents",
    "research.fetch",
    "research.search",
    "images.generate",
    "images.edit",
    "sandbox.execute",
    "development",
    "api",
    "billing",
)

_SAFETY_SCOPE = {
    "chat": "chat",
    "files": "tools",
    "documents": "tools",
    "research.fetch": "research",
    "research.search": "research",
    "images.generate": "images",
    "images.edit": "images",
    "sandbox.execute": "tools",
    "development": "tools",
}

_LABELS = {
    "chat": ("Чат", "core"),
    "projects": ("Проекты", "core"),
    "files": ("Файлы и RAG", "knowledge"),
    "documents": ("Документы", "knowledge"),
    "research.fetch": ("Загрузка веб-источников", "research"),
    "research.search": ("Поиск по интернету", "research"),
    "images.generate": ("Генерация изображений", "media"),
    "images.edit": ("Редактирование изображений", "media"),
    "sandbox.execute": ("Изолированное выполнение кода", "development"),
    "development": ("Разработка проектов", "development"),
    "api": ("API", "platform"),
    "billing": ("Платные тарифы", "commerce"),
}

_MESSAGES = {
    "account_inactive": "Аккаунт приостановлен администратором.",
    "safety_restriction_active": "Функция временно ограничена политикой доступа аккаунта.",
    "compute_quota_exhausted": "Месячный лимит локальных вычислений исчерпан.",
    "inference_not_configured": "Локальный inference runtime не настроен.",
    "file_storage_not_configured": "Хранилище файлов не настроено.",
    "document_backend_not_configured": "Рендер документов не настроен.",
    "document_worker_not_configured": "Удалённый document worker настроен не полностью.",
    "research_fetch_not_configured": "Контур получения веб-источников не настроен.",
    "search_provider_not_configured": "Поисковый провайдер не настроен.",
    "image_backend_not_configured": "Локальная генерация изображений не настроена.",
    "image_edit_backend_not_configured": "Локальное редактирование изображений не настроено.",
    "image_worker_not_running": "Image worker не запущен или его heartbeat устарел.",
    "image_edit_model_not_configured": "Модель редактирования изображений не настроена.",
    "vision_qa_not_configured": "Для строгого image QA не настроен доверенный vision endpoint.",
    "storage_quota_reached": "Квота хранения изображений исчерпана.",
    "active_image_limit": "Достигнут лимит активных image jobs.",
    "sandbox_not_configured": "Sandbox настроен не полностью.",
    "sandbox_runtime_unavailable": "Sandbox runtime сейчас недоступен.",
    "api_disabled": "Self-service API отключён конфигурацией.",
    "billing_disabled": "Покупка платных тарифов отключена.",
}

_REQUEST_RULES = (
    ("POST", "/v1/chat", "chat"),
    ("POST", "/v1/chat/stream", "chat"),
    ("POST", "/v1/research/sources", "research.fetch"),
    ("POST", "/v1/images/generations", "images.generate"),
    ("POST", "/v1/images/edits", "images.edit"),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _requirement(key: str, satisfied: bool | None, reason: str | None = None, *, mandatory: bool = True) -> dict:
    return {
        "key": key,
        "satisfied": satisfied,
        "mandatory": mandatory,
        "reason": reason,
    }


def _decision(
    capability_id: str,
    requirements: list[dict],
    *,
    details: dict | None = None,
) -> dict:
    failed = next(
        (
            item
            for item in requirements
            if item.get("mandatory", True) and item.get("satisfied") is False
        ),
        None,
    )
    reason = str(failed.get("reason") or failed.get("key")) if failed else None
    label, category = _LABELS[capability_id]
    return {
        "id": capability_id,
        "label": label,
        "category": category,
        "available": failed is None,
        "reason": reason,
        "message": _MESSAGES.get(reason, "") if reason else "",
        "requirements": requirements,
        "details": dict(details or {}),
    }


def _restriction_requirement(db: Session, user: User, capability_id: str) -> dict | None:
    safety_scope = _SAFETY_SCOPE.get(capability_id)
    if not safety_scope:
        return None
    row = active_restriction(db, user.id, safety_scope)
    return _requirement(
        "account_policy",
        row is None,
        "safety_restriction_active" if row is not None else None,
    )


def _compute_budget(db: Session, user: User, quota) -> tuple[dict, dict]:
    limit = max(0, int(quota.monthly_compute_seconds_limit))
    used = max(0, int(compute_seconds_used(db, user.id)))
    remaining = max(0, limit - used)
    requirement = _requirement(
        "monthly_compute_budget",
        remaining > 0,
        "compute_quota_exhausted" if remaining <= 0 else None,
    )
    return requirement, {
        "monthly_compute_seconds_limit": limit,
        "compute_seconds_used": used,
        "compute_seconds_remaining": remaining,
    }


def _configured_search_providers(settings) -> list[str]:
    raw = str(settings.search_providers or settings.search_provider or "")
    configured = []
    for item in raw.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if name == "searxng" and str(settings.searxng_base_url or "").strip():
            configured.append(name)
        elif name == "brave" and str(settings.brave_search_api_key or "").strip():
            configured.append(name)
    return list(dict.fromkeys(configured))


def _sandbox_config_requirements(settings) -> list[dict]:
    backend = str(settings.project_sandbox_backend or "").strip().lower()
    image = str(settings.project_sandbox_image or "").strip()
    supported = backend in {"remote", "docker", "podman", "auto"}
    rows = [
        _requirement("sandbox_backend", supported, "sandbox_not_configured" if not supported else None),
        _requirement("sandbox_image", bool(image), "sandbox_not_configured" if not image else None),
    ]
    if backend == "remote":
        token = str(settings.project_sandbox_worker_token or "").strip()
        url = str(settings.project_sandbox_worker_url or "").strip()
        ready = bool(url and token and token != "change-me-sandbox-worker")
        rows.append(
            _requirement(
                "sandbox_worker_credentials",
                ready,
                "sandbox_not_configured" if not ready else None,
            )
        )
    return rows


def _cached_live_sandbox(app) -> dict:
    now = time.monotonic()
    cached = getattr(app.state, "_capability_sandbox_probe", None)
    if isinstance(cached, dict) and now - float(cached.get("_at", 0.0)) <= 30.0:
        return {key: value for key, value in cached.items() if key != "_at"}
    settings = app.state.settings
    payload = sandbox_capabilities(settings.project_sandbox_backend, settings.project_sandbox_image)
    app.state._capability_sandbox_probe = {"_at": now, **payload}
    return payload


def capability_for_request(method: str, path: str) -> str | None:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "").rstrip("/") or "/"
    for rule_method, rule_path, capability_id in _REQUEST_RULES:
        if normalized_method == rule_method and normalized_path == rule_path:
            return capability_id
    if normalized_method == "POST":
        if normalized_path.startswith("/v1/research/runs/") and normalized_path.endswith("/discover"):
            return "research.search"
        if normalized_path.startswith("/v1/research/runs/") and normalized_path.endswith("/collect"):
            return "research.fetch"
        if normalized_path.startswith("/v1/project-sandboxes/") and normalized_path.endswith("/execute"):
            return "sandbox.execute"
        if normalized_path == "/v1/project-sandboxes/previews":
            return "sandbox.execute"
        if normalized_path.startswith("/v1/documents"):
            return "documents"
        parts = [part for part in normalized_path.split("/") if part]
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "files":
            return "files"
    return None


def capability_decision(app, db: Session, user: User, capability_id: str, *, live: bool = False) -> dict:
    if capability_id not in CAPABILITY_IDS:
        raise KeyError(capability_id)
    settings = app.state.settings
    quota = get_or_create_quota(db, user, settings)
    requirements: list[dict] = [
        _requirement(
            "account_active",
            bool(user.is_active),
            "account_inactive" if not user.is_active else None,
        )
    ]
    restriction = _restriction_requirement(db, user, capability_id)
    if restriction is not None:
        requirements.append(restriction)

    details: dict[str, Any] = {"plan": quota.plan}

    if capability_id == "chat":
        configured = bool(str(settings.llama_base_url or "").strip())
        requirements.append(_requirement("inference_endpoint", configured, "inference_not_configured" if not configured else None))
        compute_requirement, compute_details = _compute_budget(db, user, quota)
        requirements.append(compute_requirement)
        details.update(compute_details)
        initialized = getattr(app.state, "llama", None) is not None
        requirements.append(_requirement("inference_client_initialized", initialized, None, mandatory=False))
        details["model"] = str(settings.llama_model_name or "")

    elif capability_id == "projects":
        requirements.append(_requirement("project_core", True))

    elif capability_id == "files":
        ready = bool(str(settings.file_storage_path or "").strip()) and int(settings.max_file_size_bytes) > 0
        requirements.append(_requirement("file_storage", ready, "file_storage_not_configured" if not ready else None))
        details["max_file_size_bytes"] = int(settings.max_file_size_bytes)

    elif capability_id == "documents":
        backend = str(settings.document_render_backend or "").strip().lower()
        supported = backend in {"local", "remote"}
        requirements.append(_requirement("document_backend", supported, "document_backend_not_configured" if not supported else None))
        if backend == "remote":
            token = str(settings.document_render_worker_token or "").strip()
            url = str(settings.document_render_worker_url or "").strip()
            worker_ready = bool(url and token and token != "change-me-document-worker")
            requirements.append(_requirement("document_worker", worker_ready, "document_worker_not_configured" if not worker_ready else None))
        details["backend"] = backend or "disabled"

    elif capability_id == "research.fetch":
        fetch_ready = int(settings.research_max_bytes) > 0 and float(settings.research_timeout_seconds) > 0
        requirements.append(_requirement("research_fetcher", fetch_ready, "research_fetch_not_configured" if not fetch_ready else None))
        initialized = getattr(app.state, "research", None) is not None
        requirements.append(_requirement("research_fetcher_initialized", initialized, None, mandatory=False))

    elif capability_id == "research.search":
        providers = _configured_search_providers(settings)
        requirements.append(_requirement("search_provider", bool(providers), "search_provider_not_configured" if not providers else None))
        details["providers"] = providers

    elif capability_id in {"images.generate", "images.edit"}:
        worker = image_worker_snapshot(db, stale_seconds=90)
        worker_alive = bool(worker.get("alive"))

        if capability_id == "images.generate":
            configured = str(settings.image_backend or "disabled").strip().lower() != "disabled"
            requirements.append(_requirement("image_backend", configured, "image_backend_not_configured" if not configured else None))
            details["backend"] = str(settings.image_backend or "disabled")
        else:
            caps = image_edit_capabilities(settings, worker_alive=worker_alive)
            backend_ready = str(caps.get("backend") or "disabled") != "disabled" and "unsupported_image_edit_backend" not in set(caps.get("reasons") or [])
            model_ready = bool(caps.get("local_object_edit") or caps.get("identity_recompose"))
            vision_ready = (not bool(settings.image_edit_require_vision_qa)) or bool(caps.get("vision_ready"))
            requirements.extend(
                [
                    _requirement("image_edit_backend", backend_ready, "image_edit_backend_not_configured" if not backend_ready else None),
                    _requirement("image_edit_model", model_ready, "image_edit_model_not_configured" if not model_ready else None),
                    _requirement("vision_qa", vision_ready, "vision_qa_not_configured" if not vision_ready else None),
                ]
            )
            details.update(
                {
                    "backend": str(caps.get("backend") or "disabled"),
                    "local_object_edit": bool(caps.get("local_object_edit")),
                    "identity_recompose": bool(caps.get("identity_recompose")),
                }
            )

        used = total_user_image_storage_bytes(db, user.id)
        storage_ok = used < int(settings.image_user_storage_quota_bytes)
        active = int(
            db.scalar(
                select(func.count(ImageGeneration.id)).where(
                    ImageGeneration.user_id == user.id,
                    ImageGeneration.status.in_(["queued", "generating"]),
                )
            )
            or 0
        )
        slot_ok = active < int(settings.image_max_active_per_user)
        requirements.extend(
            [
                _requirement("image_worker", worker_alive, "image_worker_not_running" if not worker_alive else None),
                _requirement("image_storage_quota", storage_ok, "storage_quota_reached" if not storage_ok else None),
                _requirement("image_active_slot", slot_ok, "active_image_limit" if not slot_ok else None),
            ]
        )
        details.update(
            {
                "worker_status": worker.get("status"),
                "storage_used_bytes": int(used),
                "storage_quota_bytes": int(settings.image_user_storage_quota_bytes),
                "active_jobs": active,
                "max_active_jobs": int(settings.image_max_active_per_user),
            }
        )

    elif capability_id == "sandbox.execute":
        requirements.extend(_sandbox_config_requirements(settings))
        details["backend"] = str(settings.project_sandbox_backend or "")
        if live and all(item["satisfied"] is not False for item in requirements if item.get("mandatory", True)):
            live_caps = _cached_live_sandbox(app)
            live_ok = bool(live_caps.get("available"))
            requirements.append(_requirement("sandbox_runtime", live_ok, "sandbox_runtime_unavailable" if not live_ok else None))
            details.update(
                {
                    "runtime_available": bool(live_caps.get("runtime_available")),
                    "image_present": bool(live_caps.get("image_present")),
                    "network_isolation": bool(live_caps.get("network_isolation")),
                    "filesystem_isolation": bool(live_caps.get("filesystem_isolation")),
                }
            )
        else:
            requirements.append(_requirement("sandbox_runtime", None, None, mandatory=False))

    elif capability_id == "development":
        inference_ready = bool(str(settings.llama_base_url or "").strip())
        requirements.append(_requirement("inference_endpoint", inference_ready, "inference_not_configured" if not inference_ready else None))
        compute_requirement, compute_details = _compute_budget(db, user, quota)
        requirements.append(compute_requirement)
        details.update(compute_details)
        sandbox_rows = _sandbox_config_requirements(settings)
        sandbox_configured = all(row["satisfied"] is not False for row in sandbox_rows)
        requirements.append(_requirement("sandbox_for_execution", sandbox_configured, None, mandatory=False))

    elif capability_id == "api":
        enabled = int(settings.api_max_active_keys_per_user) > 0 and int(settings.api_max_rate_limit_per_minute) > 0
        requirements.append(_requirement("api_management", enabled, "api_disabled" if not enabled else None))
        details["max_active_keys_per_user"] = int(settings.api_max_active_keys_per_user)

    elif capability_id == "billing":
        prices = {
            "x1": int(settings.billing_price_x1_minor),
            "pro": int(settings.billing_price_pro_minor),
            "max": int(settings.billing_price_max_minor),
            "business": int(settings.billing_price_business_minor),
        }
        enabled = any(value > 0 for value in prices.values())
        requirements.append(_requirement("paid_plan_catalog", enabled, "billing_disabled" if not enabled else None))
        details["currency"] = str(settings.billing_currency or "RUB").upper()
        details["purchase_enabled_plans"] = [name for name, value in prices.items() if value > 0]

    return _decision(capability_id, requirements, details=details)


def registry_payload(app, db: Session, user: User, *, live: bool = False) -> dict:
    rows = [capability_decision(app, db, user, capability_id, live=live) for capability_id in CAPABILITY_IDS]
    return {
        "registry_version": REGISTRY_VERSION,
        "generated_at": utcnow().isoformat(),
        "user_id": user.id,
        "live_runtime_probe": bool(live),
        "capabilities": rows,
        "available": [row["id"] for row in rows if row["available"]],
        "unavailable": [row["id"] for row in rows if not row["available"]],
    }


def unavailable_http_status(decision: dict) -> int:
    reason = str(decision.get("reason") or "")
    if reason in {"account_inactive", "safety_restriction_active"}:
        return 403
    if reason in {"active_image_limit", "compute_quota_exhausted"}:
        return 429
    if reason == "storage_quota_reached":
        return 507
    return 503
