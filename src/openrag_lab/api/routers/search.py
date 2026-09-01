"""Search API endpoints backed by OpenRAG."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openrag_lab.client import OpenRAGClient, OpenRAGError
from openrag_lab.config import get_settings

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    score_threshold: float = 0.0
    rerank: bool = False
    rerank_model: str | None = None
    rerank_top_n: int | None = None
    filters: dict[str, Any] | None = None


@router.post("/api/search")
def search(body: SearchRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
            return client.search(
                body.query,
                limit=body.limit,
                score_threshold=body.score_threshold,
                rerank=body.rerank,
                rerank_model=body.rerank_model,
                rerank_top_n=body.rerank_top_n,
                filters=body.filters,
            )
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
