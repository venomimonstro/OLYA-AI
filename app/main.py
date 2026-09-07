import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes.account import router as account_router
from app.admin_ui import router as admin_ui_router
from app.media_admin_ui import router as media_admin_ui_router
from app.public_ui import router as public_ui_router
from app.user_ui import router as user_ui_router
from app.api.routes.admin import router as admin_router
from app.api.routes.safety_admin import router as safety_admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.health import router as health_router
from app.api.routes.files import router as files_router
from app.api.routes.memory import router as memory_router
from app.api.routes.projects import router as projects_router
from app.api.routes.quality import router as quality_router
from app.api.routes.research import router as research_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.usage import router as usage_router
from app.api.routes.diagnostics import router as diagnostics_router
from app.api.routes.documents import router as documents_router
from app.api.routes.code import router as code_router
from app.api.routes.images import router as images_router
from app.api.routes.media_admin import router as media_admin_router
from app.api.routes.runtime import router as runtime_router
from app.api.routes.development import router as development_router
from app.api.routes.engineering import router as engineering_router
from app.api.routes.execution import router as execution_router
from app.api.routes.sandbox import router as sandbox_router
from app.api.routes.git import router as git_router
from app.api.routes.development_chat import router as development_chat_router
from app.api.routes.operations_analytics import router as operations_router
from app.core.config import get_settings
from app.db import init_db
from app.inference.client import LlamaClient
from app.services.context import ContextCompiler
from app.services.discovery import BraveSearchDiscovery, DisabledDiscovery, ProviderPoolDiscovery
from app.services.documents import configure_render_gate
from app.services.http_limits import RequestBodyLimitMiddleware
from app.services.resource_governor import ResourceGovernor
from app.services.user_resource_governor import UserResourceGovernor
from app.services.research import ResearchFetcher
from app.services.searxng_discovery import SearxngDiscovery

logger = logging.getLogger(__name__)


def _is_prod(settings) -> bool:
    return str(settings.env).lower() in {"production", "prod", "stable"}


def _production_configuration_errors(settings) -> list[str]:
    if not _is_prod(settings):
        return []
    errors: list[str] = []
    if str(settings.database_url).lower().startswith("sqlite"):
        errors.append("production_database_must_not_be_sqlite")
    if not settings.admin_bootstrap_token or settings.admin_bootstrap_token == "change-me":
        errors.append("admin_bootstrap_token_is_default")
    if not settings.project_runtime_secret_key or settings.project_runtime_secret_key == "change-me-runtime-secret":
        errors.append("project_runtime_secret_key_is_default")
    if str(settings.project_sandbox_backend).lower() == "remote":
        token = str(settings.project_sandbox_worker_token or "")
        if not token or token == "change-me-sandbox-worker":
            errors.append("sandbox_worker_token_is_default")
    return errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    is_production = _is_prod(settings)
    production_errors = _production_configuration_errors(settings)
    if production_errors:
        raise RuntimeError("Unsafe production configuration: " + ", ".join(production_errors))
    if settings.database_auto_create_schema:
        init_db()
    app.state.settings = settings
    app.state.capacity_boot_max_context_tokens = int(settings.max_context_tokens)
    app.state.capacity_boot_deep_context_tokens = int(settings.deep_context_tokens)
    app.state.capacity_boot_max_concurrent_generations = int(settings.max_concurrent_generations)
    app.state.llama = LlamaClient(settings.llama_base_url, settings.request_timeout_seconds)
    app.state.context = ContextCompiler(max_chars=settings.deep_context_tokens * 6)
    app.state.governor = ResourceGovernor(
        max_concurrent=settings.max_concurrent_generations,
        max_queue=settings.max_queue_size,
        wait_timeout_seconds=settings.inference_queue_timeout_seconds,
    )
    app.state.research_governor = ResourceGovernor(
        max_concurrent=max(1, int(settings.research_max_concurrent_operations)),
        max_queue=max(0, int(settings.research_max_queue_size)),
        wait_timeout_seconds=max(0.5, float(settings.research_queue_timeout_seconds)),
    )
    app.state.user_governor = UserResourceGovernor()
    configure_render_gate(
        settings.document_max_concurrent_renders,
        settings.document_render_queue_timeout_seconds,
    )
    app.state.research = ResearchFetcher(
        timeout_seconds=settings.research_timeout_seconds,
        max_bytes=settings.research_max_bytes,
        max_chars=settings.research_max_chars,
        max_redirects=settings.research_max_redirects,
    )
    configured = [
        item.strip().lower()
        for item in (settings.search_providers or settings.search_provider).split(",")
        if item.strip()
    ]
    providers = []
    for name in configured:
        if name == "searxng":
            providers.append(SearxngDiscovery(settings.searxng_base_url, timeout_seconds=settings.search_timeout_seconds))
        elif name == "brave":
            providers.append(BraveSearchDiscovery(settings.brave_search_api_key, timeout_seconds=settings.search_timeout_seconds))
    app.state.discovery = ProviderPoolDiscovery(providers) if providers else DisabledDiscovery()

    beta_scheduler_task = None
    public_launch_task = None
    maintenance_task = None
    if is_production and settings.beta_operations_scheduler_enabled:
        from app.services.beta_scheduler import beta_operations_loop
        beta_scheduler_task = asyncio.create_task(beta_operations_loop(settings), name="x1-beta-operations")
        app.state.beta_operations_task = beta_scheduler_task
    if is_production and settings.public_launch_watchdog_enabled:
        from app.services.public_launch_scheduler import public_launch_watchdog_loop
        public_launch_task = asyncio.create_task(public_launch_watchdog_loop(app), name="x1-public-launch-watchdog")
        app.state.public_launch_watchdog_task = public_launch_task
    if is_production and settings.maintenance_enabled:
        from app.services.maintenance import maintenance_loop
        app.state.maintenance_last_ok_at = None
        app.state.maintenance_last_error = ""
        maintenance_task = asyncio.create_task(maintenance_loop(app), name="x1-ephemeral-maintenance")
        app.state.maintenance_task = maintenance_task
    try:
        yield
    finally:
        for task in (maintenance_task, public_launch_task, beta_scheduler_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await app.state.llama.close()


_boot_settings = get_settings()
_is_production = _is_prod(_boot_settings)
app = FastAPI(
    title="X1",
    version="0.40.0",
    description="Local-first CPU/RAM AI platform",
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=32 * 1024 * 1024)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=3)


@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/v1/", "/admin", "/media-admin", "/app")):
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    return response


@app.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    return PlainTextResponse(
        "User-agent: *\nDisallow: /v1/\nDisallow: /app\nDisallow: /admin\nDisallow: /media-admin\n",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.exception_handler(StaleDataError)
async def stale_task_state_handler(request: Request, exc: StaleDataError):
    _ = request, exc
    return JSONResponse(status_code=409, content={"detail": "Concurrent task state update conflict"})


@app.exception_handler(SQLAlchemyTimeoutError)
async def database_pool_timeout_handler(request: Request, exc: SQLAlchemyTimeoutError):
    _ = request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "Database capacity is temporarily busy; retry shortly"},
        headers={"Retry-After": "2"},
    )


@app.exception_handler(OperationalError)
async def database_operational_error_handler(request: Request, exc: OperationalError):
    _ = request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "Database is temporarily unavailable; retry shortly"},
        headers={"Retry-After": "2"},
    )


def _include_product_router(module: str) -> None:
    try:
        imported = __import__(module, fromlist=["router"])
        router = getattr(imported, "router", None)
        if router is None:
            raise RuntimeError(f"{module} does not export router")
    except (ImportError, ModuleNotFoundError, RuntimeError):
        if _is_production:
            raise
        logger.exception("Optional development router unavailable: %s", module)
        return
    app.include_router(router)


for router in (
    public_ui_router, user_ui_router, health_router, admin_ui_router, media_admin_ui_router, admin_router, operations_router,
    safety_admin_router, account_router, auth_router, projects_router, memory_router,
    files_router, conversations_router, usage_router, diagnostics_router, documents_router,
    code_router, images_router, media_admin_router, runtime_router, development_router,
    engineering_router, execution_router, sandbox_router, git_router, development_chat_router,
    quality_router, research_router, tasks_router, chat_router,
):
    app.include_router(router)

for module in (
    "app.api.routes.complaints",
    "app.api.routes.reliability",
    "app.api.routes.commerce",
    "app.api.routes.api_client",
    "app.api.routes.beta",
    "app.api.routes.beta_ops",
    "app.api.routes.launch",
    "app.beta_admin_ui",
    "app.launch_admin_ui",
):
    _include_product_router(module)
