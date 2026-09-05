from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class ResearchFetchRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=5)
    project_id: str | None = None


class ResearchSourceOut(BaseModel):
    id: str
    project_id: str | None
    url: str
    final_url: str
    title: str
    status: Literal["ready", "failed"]
    http_status: int
    media_type: str
    content_sha256: str
    char_count: int
    fetched_at: datetime
    error_message: str = ""


class ResearchSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=8, ge=1, le=20)


class ResearchExcerpt(BaseModel):
    source_id: str
    url: str
    title: str
    excerpt: str
    score: float


class ResearchSearchResponse(BaseModel):
    query: str
    excerpts: list[ResearchExcerpt]


class SourceEvidenceCreate(BaseModel):
    source_id: str
    claim: str = Field(min_length=1, max_length=5000)
    excerpt: str = Field(min_length=1, max_length=10_000)


class SourceEvidenceOut(BaseModel):
    id: str
    source_id: str
    claim: str
    excerpt: str
    state: Literal["verified_excerpt"]
    created_at: datetime

class ResearchRunCreate(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    project_id: str | None = None
    intent: Literal["general", "local_business"] = "general"
    location: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    max_results: int = Field(default=20, ge=3, le=50)


class DiscoveryHitOut(BaseModel):
    query: str
    title: str
    url: str
    snippet: str
    rank: int
    provider: str
    source_kind: str = "web"
    discovery_score: float = 0.0


class ResearchRunOut(BaseModel):
    id: str
    project_id: str | None
    question: str
    intent: str
    location: str
    category: str
    status: str
    plan: dict
    discovery_results: list[dict]
    visited_urls: list[str]
    created_at: datetime
    updated_at: datetime


class ResearchDiscoverResponse(BaseModel):
    run: ResearchRunOut
    hits: list[DiscoveryHitOut]


class ResearchCollectRequest(BaseModel):
    max_sources: int = Field(default=8, ge=1, le=20)


class ResearchCollectResponse(BaseModel):
    run: ResearchRunOut
    sources: list[ResearchSourceOut]
    failed_urls: list[str]

class BusinessCandidateOut(BaseModel):
    key: str
    display_name: str
    official_url: str | None
    source_urls: list[str]
    source_kinds: list[str]
    provider_count: int
    independent_source_count: int
    conflict_flags: list[str]
    evidence_score: float
    recommendation_state: Literal["well_supported", "supported", "insufficient_evidence"]
    public_rating: float | None
    rating_source_count: int
    review_count_total: int
    comparison_score: float | None
    comparison_state: Literal["comparable", "preliminary", "insufficient_data"]


class BusinessAnalysisOut(BaseModel):
    run_id: str
    decisive_winner: bool
    message: str
    candidates: list[BusinessCandidateOut]
