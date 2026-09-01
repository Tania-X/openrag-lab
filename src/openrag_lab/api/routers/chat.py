"""Chat API endpoints backed by OpenRAG."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openrag_lab.client import OpenRAGClient, OpenRAGError
from openrag_lab.config import get_settings

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    limit: int = 10
    score_threshold: float = 0.0
    filters: dict[str, Any] | None = None


@router.post("/api/chat")
def chat(body: ChatRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
            return client.chat(
                body.message,
                limit=body.limit,
                score_threshold=body.score_threshold,
                filters=body.filters,
            )
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
