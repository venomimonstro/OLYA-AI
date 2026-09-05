from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes.account import router as account_router
from app.admin_ui import router as admin_ui_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.complaints import router as complaints_router
from app.core.config import get_settings
from app.db import init_db

# The remaining product routers are loaded lazily so an incomplete optional
# deployment cannot prevent auth/admin/complaint recovery endpoints from booting.
def _optional_router(module: str):
    try:
        mod = __import__(module, fromlist=["router"])
        return getattr(mod, "router", None)
    except (ImportError, ModuleNotFoundError):
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_auto_create_schema:
        init_db()
    app.state.settings = settings

    # Heavy/local capabilities remain optional at boot and are initialized by
    # their native modules when present. This keeps admin/recovery paths alive
    # during partial or degraded self-hosted deployments.
    try:
        from app.inference.client import LlamaClient
        app.state.llama = LlamaClient(settings.llama_base_url, settings.request_timeout_seconds)
    except (ImportError, ModuleNotFoundError):
        app.state.llama = None
    try:
        from app.services.context import ContextCompiler
        app.state.context = ContextCompiler(max_chars=settings.max_context_tokens * 6)
    except (ImportError, ModuleNotFoundError):
        app.state.context = None
    try:
        from app.services.resource_governor import ResourceGovernor
        app.state.governor = ResourceGovernor(max_concurrent=settings.max_concurrent_generations, max_queue=settings.max_queue_size)
    except (ImportError, ModuleNotFoundError):
        app.state.governor = None
    try:
        from app.services.user_resource_governor import UserResourceGovernor
        app.state.user_governor = UserResourceGovernor()
    except (ImportError, ModuleNotFoundError):
        app.state.user_governor = None
    yield


app = FastAPI(title="X1", version="0.30.0", description="Local-first CPU/RAM AI platform", lifespan=lifespan)


@app.exception_handler(StaleDataError)
async def stale_task_state_handler(request: Request, exc: StaleDataError):
    _ = request, exc
    return JSONResponse(status_code=409, content={"detail": "Concurrent task state update conflict"})


for required in (admin_ui_router, admin_router, account_router, auth_router, complaints_router):
    app.include_router(required)

for module in (
    "app.media_admin_ui",
    "app.api.routes.safety_admin",
    "app.api.routes.health",
    "app.api.routes.projects",
    "app.api.routes.memory",
    "app.api.routes.files",
    "app.api.routes.conversations",
    "app.api.routes.usage",
    "app.api.routes.diagnostics",
    "app.api.routes.documents",
    "app.api.routes.code",
    "app.api.routes.images",
    "app.api.routes.media_admin",
    "app.api.routes.runtime",
    "app.api.routes.development",
    "app.api.routes.engineering",
    "app.api.routes.execution",
    "app.api.routes.sandbox",
    "app.api.routes.git",
    "app.api.routes.development_chat",
    "app.api.routes.quality",
    "app.api.routes.research",
    "app.api.routes.tasks",
    "app.api.routes.chat",
):
    router = _optional_router(module)
    if router is not None:
        app.include_router(router)
