from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ResearchSource, SourceEvidence, User
from app.schemas.research import (
    ResearchExcerpt,
    ResearchFetchRequest,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchSourceOut,
    SourceEvidenceCreate,
    SourceEvidenceOut,
)
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.research import ResearchFetchError, UnsafeURL, exact_evidence, lexical_excerpts, source_sha256


router = APIRouter(prefix="/v1/research", tags=["research"])


def _can_read_source(db: Session, user: User, source: ResearchSource) -> bool:
    if source.project_id:
        try:
            require_project_role(db, user, source.project_id, "viewer")
        except HTTPException:
            return False
        return True
    return source.user_id == user.id


def _source_out(item: ResearchSource) -> ResearchSourceOut:
    return ResearchSourceOut(
        id=item.id,
        project_id=item.project_id,
        url=item.url,
        final_url=item.final_url,
        title=item.title,
        status=item.status,
        http_status=item.http_status,
        media_type=item.media_type,
        content_sha256=item.content_sha256,
        char_count=len(item.content),
        fetched_at=item.fetched_at,
        error_message=item.error_message,
    )


@router.post("/sources", response_model=list[ResearchSourceOut])
async def fetch_sources(
    payload: ResearchFetchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    result: list[ResearchSource] = []
    for raw_url in payload.urls:
        requested = str(raw_url)
        try:
            page = await request.app.state.research.fetch(requested)
        except (UnsafeURL, ResearchFetchError) as exc:
            # Unsafe URLs are rejected without persisting internal addresses.
            if isinstance(exc, UnsafeURL):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            source = ResearchSource(
                user_id=user.id,
                project_id=payload.project_id,
                url=requested,
                final_url=requested,
                title="",
                content="",
                content_sha256=source_sha256(""),
                http_status=0,
                media_type="",
                status="failed",
                error_message=str(exc)[:1000],
            )
        else:
            digest = source_sha256(page.content)
            same_scope = [
                ResearchSource.user_id == user.id,
                ResearchSource.final_url == page.final_url,
                ResearchSource.content_sha256 == digest,
                ResearchSource.status == "ready",
            ]
            if payload.project_id is None:
                same_scope.append(ResearchSource.project_id.is_(None))
            else:
                same_scope.append(ResearchSource.project_id == payload.project_id)
            source = db.scalar(select(ResearchSource).where(*same_scope).order_by(ResearchSource.fetched_at.desc()).limit(1))
            if source is not None:
                from app.models import utcnow
                source.fetched_at = utcnow()
                source.http_status = page.http_status
                source.media_type = page.media_type
                source.title = page.title
            else:
                source = ResearchSource(
                    user_id=user.id,
                    project_id=payload.project_id,
                    url=page.requested_url,
                    final_url=page.final_url,
                    title=page.title,
                    content=page.content,
                    content_sha256=digest,
                    http_status=page.http_status,
                    media_type=page.media_type,
                    status="ready",
                )
        db.add(source)
        db.flush()
        result.append(source)
    db.commit()
    return [_source_out(item) for item in result]


@router.get("/sources", response_model=list[ResearchSourceOut])
def list_sources(
    project_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if project_id:
        require_project_role(db, user, project_id, "viewer")
        stmt = select(ResearchSource).where(ResearchSource.project_id == project_id)
    else:
        stmt = select(ResearchSource).where(ResearchSource.user_id == user.id, ResearchSource.project_id.is_(None))
    rows = list(db.scalars(stmt.order_by(ResearchSource.fetched_at.desc()).limit(100)).all())
    return [_source_out(item) for item in rows]


@router.get("/sources/{source_id}", response_model=ResearchSourceOut)
def get_source(source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source = db.get(ResearchSource, source_id)
    if source is None or not _can_read_source(db, user, source):
        raise HTTPException(status_code=404, detail="Source not found")
    return _source_out(source)


@router.post("/search", response_model=ResearchSearchResponse)
def search_sources(
    payload: ResearchSearchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    excerpts: list[ResearchExcerpt] = []
    for source_id in payload.source_ids:
        source = db.get(ResearchSource, source_id)
        if source is None or source.status != "ready" or not _can_read_source(db, user, source):
            continue
        for excerpt, score in lexical_excerpts(source.content, payload.query, limit=payload.limit):
            excerpts.append(
                ResearchExcerpt(source_id=source.id, url=source.final_url, title=source.title, excerpt=excerpt, score=score)
            )
    excerpts.sort(key=lambda item: item.score, reverse=True)
    return ResearchSearchResponse(query=payload.query, excerpts=excerpts[: payload.limit])


@router.post("/evidence", response_model=SourceEvidenceOut, status_code=201)
def create_evidence(
    payload: SourceEvidenceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = db.get(ResearchSource, payload.source_id)
    if source is None or source.status != "ready" or not _can_read_source(db, user, source):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        evidence = exact_evidence(db, source=source, claim=payload.claim, excerpt=payload.excerpt, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return SourceEvidenceOut(
        id=evidence.id,
        source_id=evidence.source_id,
        claim=evidence.claim,
        excerpt=evidence.excerpt,
        state=evidence.state,
        created_at=evidence.created_at,
    )


@router.get("/evidence/{evidence_id}", response_model=SourceEvidenceOut)
def get_evidence(evidence_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    evidence = db.get(SourceEvidence, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    source = db.get(ResearchSource, evidence.source_id)
    if source is None or not _can_read_source(db, user, source):
        raise HTTPException(status_code=404, detail="Evidence not found")
    return SourceEvidenceOut(
        id=evidence.id,
        source_id=evidence.source_id,
        claim=evidence.claim,
        excerpt=evidence.excerpt,
        state=evidence.state,
        created_at=evidence.created_at,
    )

# --- Sprint 7: persisted research planning and search discovery ---
from app.models import ResearchRun
from app.schemas.research import ResearchRunCreate, ResearchRunOut, ResearchDiscoverResponse, DiscoveryHitOut, ResearchCollectRequest, ResearchCollectResponse
from app.services.discovery import DiscoveryError, dedupe_hits, enrich_hit, cached_provider_search
from app.services.research_planner import plan_research


def _run_out(item: ResearchRun) -> ResearchRunOut:
    return ResearchRunOut(
        id=item.id,
        project_id=item.project_id,
        question=item.question,
        intent=item.intent,
        location=item.location,
        category=item.category,
        status=item.status,
        plan=item.plan or {},
        discovery_results=item.discovery_results or [],
        visited_urls=item.visited_urls or [],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _can_read_run(db: Session, user: User, run: ResearchRun) -> bool:
    if run.project_id:
        try:
            require_project_role(db, user, run.project_id, "viewer")
        except HTTPException:
            return False
        return True
    return run.user_id == user.id


@router.post("/runs", response_model=ResearchRunOut, status_code=201)
def create_research_run(
    payload: ResearchRunCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    try:
        plan = plan_research(
            payload.question,
            intent=payload.intent,
            location=payload.location,
            category=payload.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = request.app.state.settings
    max_queries = min(len(plan.queries), settings.research_max_search_queries)
    run = ResearchRun(
        user_id=user.id,
        project_id=payload.project_id,
        question=payload.question,
        intent=plan.intent,
        location=(payload.location or "").strip(),
        category=(payload.category or "").strip(),
        status="planned",
        plan={"queries": plan.queries[:max_queries], "freshness": plan.freshness, "source_mix": plan.source_mix},
        max_queries=max_queries,
        max_results=min(payload.max_results, settings.research_max_discovery_results),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _run_out(run)


@router.get("/runs/{run_id}", response_model=ResearchRunOut)
def get_research_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if run is None or not _can_read_run(db, user, run):
        raise HTTPException(status_code=404, detail="Research run not found")
    return _run_out(run)


@router.post("/runs/{run_id}/discover", response_model=ResearchDiscoverResponse)
async def discover_research_sources(
    run_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(ResearchRun, run_id)
    if run is None or not _can_read_run(db, user, run):
        raise HTTPException(status_code=404, detail="Research run not found")
    if run.project_id:
        require_project_role(db, user, run.project_id, "member")
    elif run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Research run not found")
    queries = list((run.plan or {}).get("queries") or [])[: run.max_queries]
    gathered = []
    try:
        for query in queries:
            remaining = max(run.max_results - len(gathered), 0)
            if remaining <= 0:
                break
            rows = await cached_provider_search(db, request.app.state.discovery, query, count=min(10, remaining), country="RU", language="ru", ttl_seconds=request.app.state.settings.search_cache_ttl_seconds, quality_mode=(run.intent == "local_business"))
            gathered.extend(rows)
    except DiscoveryError as exc:
        run.status = "waiting_search"
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    hits = dedupe_hits(gathered, limit=run.max_results)
    run.discovery_results = [enrich_hit(h) for h in hits]
    run.status = "discovered" if hits else "no_results"
    db.commit()
    db.refresh(run)
    return ResearchDiscoverResponse(
        run=_run_out(run),
        hits=[DiscoveryHitOut(**row) for row in run.discovery_results],
    )


@router.post("/runs/{run_id}/collect", response_model=ResearchCollectResponse)
async def collect_research_sources(
    run_id: str,
    payload: ResearchCollectRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(ResearchRun, run_id)
    if run is None or not _can_read_run(db, user, run):
        raise HTTPException(status_code=404, detail="Research run not found")
    if run.project_id:
        require_project_role(db, user, run.project_id, "member")
    elif run.user_id != user.id:
        raise HTTPException(status_code=404, detail="Research run not found")
    rows = sorted(
        list(run.discovery_results or []),
        key=lambda row: (float(row.get("discovery_score", 0)), -int(row.get("rank", 999))),
        reverse=True,
    )
    visited = set(run.visited_urls or [])
    selected = [row for row in rows if row.get("url") and row["url"] not in visited][: payload.max_sources]
    sources: list[ResearchSource] = []
    failed: list[str] = []
    for row in selected:
        url = str(row["url"])
        visited.add(url)
        try:
            page = await request.app.state.research.fetch(url)
        except (UnsafeURL, ResearchFetchError):
            failed.append(url)
            continue
        digest = source_sha256(page.content)
        scope = [
            ResearchSource.user_id == user.id,
            ResearchSource.final_url == page.final_url,
            ResearchSource.content_sha256 == digest,
            ResearchSource.status == "ready",
        ]
        if run.project_id:
            scope.append(ResearchSource.project_id == run.project_id)
        else:
            scope.append(ResearchSource.project_id.is_(None))
        source = db.scalar(select(ResearchSource).where(*scope).order_by(ResearchSource.fetched_at.desc()).limit(1))
        if source is None:
            source = ResearchSource(
                user_id=user.id, project_id=run.project_id, url=page.requested_url, final_url=page.final_url,
                title=page.title, content=page.content, content_sha256=digest, http_status=page.http_status,
                media_type=page.media_type, status="ready",
            )
            db.add(source)
            db.flush()
        sources.append(source)
    run.visited_urls = sorted(visited)
    run.status = "collected" if sources else ("collection_partial" if failed else run.status)
    db.commit()
    db.refresh(run)
    return ResearchCollectResponse(run=_run_out(run), sources=[_source_out(item) for item in sources], failed_urls=failed)

# --- Sprint 8: Local Business Intelligence ---
from app.schemas.research import BusinessAnalysisOut, BusinessCandidateOut
from app.services.business_intelligence import build_business_candidates, recommendation_summary


@router.post("/runs/{run_id}/businesses/analyze", response_model=BusinessAnalysisOut)
def analyze_local_businesses(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(ResearchRun, run_id)
    if run is None or not _can_read_run(db, user, run):
        raise HTTPException(status_code=404, detail="Research run not found")
    if run.intent != "local_business":
        raise HTTPException(status_code=422, detail="Research run is not a local business analysis")
    if run.project_id:
        require_project_role(db, user, run.project_id, "viewer")
    candidates = build_business_candidates(list(run.discovery_results or []))
    summary = recommendation_summary(candidates)
    candidate_rows = [c.__dict__ for c in candidates[:10]]
    run.business_analysis = {
        "decisive_winner": summary["decisive_winner"],
        "message": summary["message"],
        "candidates": candidate_rows,
    }
    db.commit()
    return BusinessAnalysisOut(
        run_id=run.id,
        decisive_winner=summary["decisive_winner"],
        message=summary["message"],
        candidates=[BusinessCandidateOut(**row) for row in candidate_rows],
    )


@router.get("/runs/{run_id}/businesses", response_model=BusinessAnalysisOut)
def get_local_business_analysis(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(ResearchRun, run_id)
    if run is None or not _can_read_run(db, user, run):
        raise HTTPException(status_code=404, detail="Research run not found")
    data = run.business_analysis or {}
    if not data:
        raise HTTPException(status_code=404, detail="Business analysis not found")
    return BusinessAnalysisOut(run_id=run.id, **data)
