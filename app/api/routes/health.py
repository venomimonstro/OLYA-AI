from pathlib import Path
import json
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.fastapi_route_compat import install_system_observability_compat
from app.services.system_observability import CRITICAL, collect_system_health

# FastAPI 0.137+ keeps include_router() branches lazy in app.routes. Install the
# prefix-aware compatibility probe before any readiness/reliability checks run.
install_system_observability_compat()

router = APIRouter(tags=["health"])
_BUILD_PROVENANCE = Path("/app/BUILD_PROVENANCE.json")
_INSTANCE_ID = secrets.token_hex(8)


def _runtime_provenance() -> dict:
    try:
        payload = json.loads(_BUILD_PROVENANCE.read_text("utf-8"))
    except Exception:
        return {"format": "x1-build-provenance-v1", "source_fingerprint": "", "file_count": 0}
    fingerprint = str(payload.get("source_fingerprint") or "").lower()
    valid = len(fingerprint) == 64 and all(char in "0123456789abcdef" for char in fingerprint)
    return {
        "format": str(payload.get("format") or ""),
        "source_fingerprint": fingerprint if valid else "",
        "file_count": int(payload.get("file_count") or 0),
    }


@router.get("/health")
async def health() -> dict[str, str]:
    # instance_id changes on every app-process restart. Browser clients use it
    # to distinguish a transient network interruption from a server restart,
    # where old in-memory chat runs cannot be resumed safely.
    return {"status": "ok", "instance_id": _INSTANCE_ID}


@router.get("/version")
async def version(request: Request) -> dict:
    payload = _runtime_provenance()
    settings = getattr(request.app.state, "settings", None)
    payload["runtime_profile"] = str(getattr(settings, "server_optimization_profile", "unknown") or "unknown")
    payload["model"] = str(getattr(settings, "llama_model_name", "") or "")
    payload["instance_id"] = _INSTANCE_ID
    return payload


@router.get("/ready")
async def ready(request: Request, db: Session = Depends(get_db)):
    result = await collect_system_health(request.app, db, persist=False, deep=False)
    critical = result["status"] == CRITICAL
    body = {
        "status": "not_ready" if critical else result["status"],
        "score": result["score"],
        "components": {item["key"]: item["status"] for item in result["checks"]},
        "checked_at": result["checked_at"],
        "instance_id": _INSTANCE_ID,
    }
    return JSONResponse(status_code=503 if critical else 200, content=jsonable_encoder(body))
