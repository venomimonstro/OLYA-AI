from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.models import User
from app.services.auth import get_current_user
from app.services.local_business_index import discover_local_businesses, local_index_stats
from app.services.local_search_store import get_local_search_store

router = APIRouter(prefix="/v1/owned-search", tags=["owned-search"])


@router.get("/status")
def owned_search_status(user: User = Depends(get_current_user)) -> dict:
    _ = user
    stats = get_local_search_store().stats()
    return {
        "ready": int(stats.get("businesses") or 0) > 0 or int(stats.get("pages") or 0) > 0,
        "backend": "sqlite_fts5_rtree",
        "businesses": int(stats.get("businesses") or 0),
        "pages": int(stats.get("pages") or 0),
        "queued": int(stats.get("queued") or 0),
        "path": str(stats.get("path") or ""),
    }


@router.get("/businesses")
def owned_business_search(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=10, ge=1, le=30),
    user: User = Depends(get_current_user),
) -> dict:
    _ = user
    rows = discover_local_businesses(q, limit=limit)
    return {
        "query": q,
        "count": len(rows),
        "results": [
            {
                "name": row.name,
                "category_source": row.provider,
                "address": row.address,
                "phone": row.phone,
                "website": row.website,
                "card_url": row.card_url,
            }
            for row in rows
        ],
        "stats": local_index_stats(),
    }


@router.get("/web")
def owned_web_search(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=8, ge=1, le=30),
    user: User = Depends(get_current_user),
) -> dict:
    _ = user
    rows = get_local_search_store().search_pages(q, limit=limit)
    return {
        "query": q,
        "count": len(rows),
        "results": [
            {
                "title": row.title,
                "url": row.url,
                "domain": row.domain,
                "description": row.description,
                "snippet": row.content[:600],
                "modified_at": row.modified_at,
                "fetched_at": row.fetched_at,
                "score": row.score,
            }
            for row in rows
        ],
    }
