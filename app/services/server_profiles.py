from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

from scripts.download_model import (
    LLAMA_MEMORY_CAP_GIB,
    LLAMA_MIN_MEMORY_GIB,
    MIN_DETECTED_RAM_GIB,
    NON_LLAMA_RESERVE_GIB,
    safe_context_for_ram_gib,
)

PROFILE_NAMES = ("super_low", "optimal", "maximum")
REQUEST_FILE = "server-profile-request.json"
ACTIVE_FILE = "server-profile-active.json"


@dataclass(frozen=True)
class ServerProfileEnvelope:
    profile: str
    host_ram_gib: float
    cpu_cores: int
    safe_context_ceiling: int
    context_tokens: int
    llama_memory_gib: int
    llama_threads: int
    max_concurrent_generations: int
    inference_queue: int
    inference_queue_timeout_seconds: int
    database_pool_size: int
    database_max_overflow: int
    app_memory_mb: int
    db_memory_mb: int
    searx_memory_mb: int
    sandbox_worker_memory_mb: int
    document_worker_memory_mb: int
    sandbox_max_memory_mb: int
    project_runtime_max_memory_mb: int
    document_concurrency: int
    research_concurrency: int
    research_queue: int
    chat_http_active: int
    chat_http_queue: int
    research_http_active: int
    research_http_queue: int
    image_http_active: int
    image_http_queue: int
    sandbox_http_active: int
    sandbox_http_queue: int
    images_default_enabled: bool

    def env(self) -> dict[str, str]:
        return {
            "X1_SERVER_OPTIMIZATION_PROFILE": self.profile,
            "X1_MAX_CONTEXT_TOKENS": str(min(8192, self.context_tokens)),
            "X1_DEEP_CONTEXT_TOKENS": str(self.context_tokens),
            "X1_LLAMA_MEMORY_LIMIT": f"{self.llama_memory_gib}g",
            "X1_LLAMA_THREADS": str(self.llama_threads),
            "X1_LLAMA_THREADS_BATCH": str(self.llama_threads),
            "X1_MAX_CONCURRENT_GENERATIONS": str(self.max_concurrent_generations),
            "X1_MAX_QUEUE_SIZE": str(self.inference_queue),
            "X1_INFERENCE_QUEUE_TIMEOUT_SECONDS": str(self.inference_queue_timeout_seconds),
            "X1_DATABASE_POOL_SIZE": str(self.database_pool_size),
            "X1_DATABASE_MAX_OVERFLOW": str(self.database_max_overflow),
            "X1_APP_MEMORY_LIMIT_MB": str(self.app_memory_mb),
            "X1_DB_MEMORY_LIMIT_MB": str(self.db_memory_mb),
            "X1_SEARX_MEMORY_LIMIT_MB": str(self.searx_memory_mb),
            "X1_SANDBOX_WORKER_MEMORY_LIMIT_MB": str(self.sandbox_worker_memory_mb),
            "X1_DOCUMENT_WORKER_MEMORY_LIMIT_MB": str(self.document_worker_memory_mb),
            "X1_SANDBOX_MAX_MEMORY_MB": str(self.sandbox_max_memory_mb),
            "X1_PROJECT_RUNTIME_MAX_MEMORY_MB": str(self.project_runtime_max_memory_mb),
            "X1_PROJECT_RUNTIME_DEFAULT_MEMORY_MB": str(min(1024, self.project_runtime_max_memory_mb)),
            "X1_DOCUMENT_MAX_CONCURRENT_RENDERS": str(self.document_concurrency),
            "X1_RESEARCH_MAX_CONCURRENT_OPERATIONS": str(self.research_concurrency),
            "X1_RESEARCH_MAX_QUEUE_SIZE": str(self.research_queue),
            "X1_OVERLOAD_CHAT_MAX_ACTIVE_HTTP": str(self.chat_http_active),
            "X1_OVERLOAD_CHAT_MAX_QUEUE": str(self.chat_http_queue),
            "X1_OVERLOAD_RESEARCH_MAX_ACTIVE_HTTP": str(self.research_http_active),
            "X1_OVERLOAD_RESEARCH_MAX_QUEUE": str(self.research_http_queue),
            "X1_OVERLOAD_IMAGE_MAX_ACTIVE_HTTP": str(self.image_http_active),
            "X1_OVERLOAD_IMAGE_MAX_QUEUE": str(self.image_http_queue),
            "X1_OVERLOAD_SANDBOX_MAX_ACTIVE_HTTP": str(self.sandbox_http_active),
            "X1_OVERLOAD_SANDBOX_MAX_QUEUE": str(self.sandbox_http_queue),
        }


def _threads(cores: int, *, low: bool = False) -> int:
    value = max(2, min(24, max(2, int(cores)) - 1))
    return max(2, min(value, 8)) if low else value


def _base(name: str, ram_gib: float, cpu_cores: int, safe_context: int, llama_memory: int) -> dict:
    return {
        "profile": name,
        "host_ram_gib": float(ram_gib),
        "cpu_cores": max(1, int(cpu_cores)),
        "safe_context_ceiling": int(safe_context),
        "llama_memory_gib": int(llama_memory),
        "max_concurrent_generations": 1,
        "images_default_enabled": False,
    }


def profile_for_host(profile: str, ram_gib: float, cpu_cores: int) -> ServerProfileEnvelope:
    name = str(profile or "optimal").strip().lower()
    if name not in PROFILE_NAMES:
        raise ValueError(f"Unknown server profile: {profile}")
    safe_context = safe_context_for_ram_gib(float(ram_gib))
    floor_ram = int(float(ram_gib))
    llama_available = max(0, floor_ram - NON_LLAMA_RESERVE_GIB)
    llama_memory = min(LLAMA_MEMORY_CAP_GIB, llama_available)
    if llama_memory < LLAMA_MIN_MEMORY_GIB:
        raise ValueError(
            f"Host has insufficient RAM for production Qwen: {ram_gib:.2f} GiB; "
            f"minimum detected is {MIN_DETECTED_RAM_GIB} GiB"
        )

    if name == "super_low":
        return ServerProfileEnvelope(**_base(name, ram_gib, cpu_cores, safe_context, LLAMA_MIN_MEMORY_GIB),
            context_tokens=min(4096, safe_context), llama_threads=_threads(cpu_cores, low=True),
            inference_queue=12, inference_queue_timeout_seconds=45,
            database_pool_size=4, database_max_overflow=2,
            app_memory_mb=1536, db_memory_mb=1024, searx_memory_mb=512,
            sandbox_worker_memory_mb=256, document_worker_memory_mb=512,
            sandbox_max_memory_mb=512, project_runtime_max_memory_mb=512,
            document_concurrency=1, research_concurrency=1, research_queue=8,
            chat_http_active=2, chat_http_queue=8,
            research_http_active=1, research_http_queue=6,
            image_http_active=1, image_http_queue=2,
            sandbox_http_active=1, sandbox_http_queue=4)

    if name == "optimal":
        return ServerProfileEnvelope(**_base(name, ram_gib, cpu_cores, safe_context, LLAMA_MIN_MEMORY_GIB),
            context_tokens=min(8192, safe_context), llama_threads=_threads(cpu_cores),
            inference_queue=32, inference_queue_timeout_seconds=90,
            database_pool_size=8, database_max_overflow=4,
            app_memory_mb=2048, db_memory_mb=1536, searx_memory_mb=768,
            sandbox_worker_memory_mb=384, document_worker_memory_mb=768,
            sandbox_max_memory_mb=1024, project_runtime_max_memory_mb=1024,
            document_concurrency=1, research_concurrency=4, research_queue=32,
            chat_http_active=4, chat_http_queue=32,
            research_http_active=4, research_http_queue=24,
            image_http_active=2, image_http_queue=8,
            sandbox_http_active=2, sandbox_http_queue=8)

    medium = ram_gib >= 47.0
    large = ram_gib >= 63.0
    return ServerProfileEnvelope(**_base(name, ram_gib, cpu_cores, safe_context, llama_memory),
        context_tokens=safe_context, llama_threads=_threads(cpu_cores),
        inference_queue=48 if medium else 32, inference_queue_timeout_seconds=120,
        database_pool_size=12 if large else 10 if medium else 8,
        database_max_overflow=6 if large else 4,
        app_memory_mb=3072 if medium else 2048,
        db_memory_mb=2048 if medium else 1536,
        searx_memory_mb=1024 if medium else 768,
        sandbox_worker_memory_mb=512 if medium else 384,
        document_worker_memory_mb=1024 if medium else 768,
        sandbox_max_memory_mb=2048 if large else 1536 if medium else 1024,
        project_runtime_max_memory_mb=2048 if large else 1536 if medium else 1024,
        document_concurrency=2 if medium else 1,
        research_concurrency=8 if large else 6 if medium else 4,
        research_queue=48 if medium else 32,
        chat_http_active=6 if medium else 4, chat_http_queue=48 if medium else 32,
        research_http_active=6 if medium else 4, research_http_queue=32 if medium else 24,
        image_http_active=3 if medium else 2, image_http_queue=12 if medium else 8,
        sandbox_http_active=3 if medium else 2, sandbox_http_queue=12 if medium else 8)


def profile_payload(profile: str, ram_gib: float, cpu_cores: int) -> dict:
    envelope = profile_for_host(profile, ram_gib, cpu_cores)
    return {"format": "x1-server-profile-v1", "envelope": asdict(envelope), "env": envelope.env()}


def _atomic_json(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(target)
    return target


def stage_profile_request(data_root: str | Path, profile: str, ram_gib: float, cpu_cores: int) -> Path:
    return _atomic_json(Path(data_root).resolve() / REQUEST_FILE, profile_payload(profile, ram_gib, cpu_cores))


def persist_active_profile(data_root: str | Path, profile: str, ram_gib: float, cpu_cores: int) -> Path:
    payload = profile_payload(profile, ram_gib, cpu_cores)
    payload["status"] = "active"
    return _atomic_json(Path(data_root).resolve() / ACTIVE_FILE, payload)


def _read_profile(path: Path, expected_status: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "invalid", "path": str(path)}
    if payload.get("format") != "x1-server-profile-v1":
        return {"status": "invalid", "path": str(path)}
    return {"status": payload.get("status") or expected_status, "path": str(path), **payload}


def read_staged_profile(data_root: str | Path) -> dict | None:
    return _read_profile(Path(data_root).resolve() / REQUEST_FILE, "staged")


def read_active_profile(data_root: str | Path) -> dict | None:
    return _read_profile(Path(data_root).resolve() / ACTIVE_FILE, "active")
