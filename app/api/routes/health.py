from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str | bool]:
    llama_ok = await request.app.state.llama.health()
    return {"status": "ready" if llama_ok else "degraded", "local_inference": llama_ok}
