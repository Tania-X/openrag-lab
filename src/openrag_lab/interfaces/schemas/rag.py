"""Request/response contracts for search and chat.

``filters`` is deliberately absent: the tenant boundary is built server-side
from the caller's registered documents, so a client cannot widen or replace it.
Unknown fields are rejected rather than ignored, so a caller that tries to send
``filters`` gets a clear error instead of believing it took effect.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(_Request):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank: bool = False
    rerank_model: str | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    # Only honoured for super_admin; other callers are rejected with 403.
    tenant_id: str | None = None


class ChatRequest(_Request):
    message: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    tenant_id: str | None = None


class SearchScopeOut(BaseModel):
    tenant_id: str
    document_count: int
    cross_tenant: bool


class SearchOut(BaseModel):
    results: list[dict]
    scope: SearchScopeOut


class ChatOut(BaseModel):
    response: str | None = None
    scope: SearchScopeOut
