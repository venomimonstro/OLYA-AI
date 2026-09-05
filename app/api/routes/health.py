from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.system_observability import CRITICAL, collect_system_health

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, db: Session = Depends(get_db)):
    result = await collect_system_health(request.app, db, persist=False, deep=False)
    critical = result["status"] == CRITICAL
    body = {
        "status": "not_ready" if critical else result["status"],
        "score": result["score"],
        "components": {item["key"]: item["status"] for item in result["checks"]},
        "checked_at": result["checked_at"],
    }
    return JSONResponse(status_code=503 if critical else 200, content=jsonable_encoder(body))
