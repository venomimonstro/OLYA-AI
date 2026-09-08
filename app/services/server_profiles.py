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
        return ServerProfileEnvelope(
            name, ram_gib, cpu_cores, safe_context, min(4096, safe_context), LLAMA_MIN_MEMORY_GIB,
            _threads(cpu_cores, low=True), 1, 12, 45, 4, 2, 1536, 1024, 512, 256, 512,
            512, 512, 1, 1, 8, 2, 8, 1, 6, 1, 2, 1, 4, False,
        )
    if name == "optimal":
        return ServerProfileEnvelope(
            name, ram_gib, cpu_cores, safe_context, min(8192, safe_context), LLAMA_MIN_MEMORY_GIB,
            _threads(cpu_cores), 1, 32, 90, 8, 4, 2048, 1536, 768, 384, 768,
            1024, 1024, 1, 4, 32, 4, 32, 4, 24, 2, 8, 2, 8, False,
        )

    # Maximum expands only inside the boot envelope of the actual host. Qwen
    # remains one generation slot: extra RAM is used for context/control-plane
    # headroom instead of creating a second CPU-heavy model generation.
    large = ram_gib >= 63.0
    medium = ram_gib >= 47.0
    return ServerProfileEnvelope(
        name, ram_gib, cpu_cores, safe_context, safe_context, llama_memory,
        _threads(cpu_cores), 1, 48 if medium else 32, 120,
        12 if large else 10 if medium else 8, 6 if large else 4,
        3072 if medium else 2048, 2048 if medium else 1536, 1024 if medium else 768,
        512 if medium else 384, 1024 if medium else 768,
        2048 if large else 1536 if medium else 1024,
        2048 if large else 1536 if medium else 1024,
        2 if medium else 1, 8 if large else 6 if medium else 4, 48 if medium else 32,
        6 if medium else 4, 48 if medium else 32,
        6 if medium else 4, 32 if medium else 24,
        3 if medium else 2, 12 if medium else 8,
        3 if medium else 2, 12 if medium else 8,
        False,
    )


def profile_payload(profile: str, ram_gib: float, cpu_cores: int) -> dict:
    envelope = profile_for_host(profile, ram_gib, cpu_cores)
    return {"format": "x1-server-profile-v1", "envelope": asdict(envelope), "env": envelope.env()}


def stage_profile_request(data_root: str | Path, profile: str, ram_gib: float, cpu_cores: int) -> Path:
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / REQUEST_FILE
    payload = profile_payload(profile, ram_gib, cpu_cores)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(target)
    return target


def read_staged_profile(data_root: str | Path) -> dict | None:
    target = Path(data_root).resolve() / REQUEST_FILE
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "invalid", "path": str(target)}
    if payload.get("format") != "x1-server-profile-v1":
        return {"status": "invalid", "path": str(target)}
    return {"status": "staged", "path": str(target), **payload}
