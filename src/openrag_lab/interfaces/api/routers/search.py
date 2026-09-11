"""Tenant-scoped semantic search endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.application.rag.search_service import SearchService
from openrag_lab.client import OpenRAGError
from openrag_lab.interfaces.api.deps import (
    CurrentUser,
    DbSession,
    RagGatewayDep,
    require_permission,
)
from openrag_lab.interfaces.schemas.rag import SearchOut, SearchRequest

router = APIRouter(tags=["search"])


@router.post("/api/search", response_model=SearchOut)
async def search(
    body: SearchRequest,
    session: DbSession,
    gateway: RagGatewayDep,
    actor: Annotated[CurrentUser, Depends(require_permission("search:use"))],
) -> dict:
    """Search the documents owned by the caller's tenant.

    The tenant boundary comes from the document registry, never from the body.
    """
    service = SearchService(RetrievalScopeResolver(session), gateway)
    try:
        return await service.search(
            actor_user_id=actor.user_id,
            actor_tenant_id=actor.tenant_id,
            query=body.query,
            limit=body.limit,
            score_threshold=body.score_threshold,
            rerank=body.rerank,
            rerank_model=body.rerank_model,
            rerank_top_n=body.rerank_top_n,
            tenant_id=body.tenant_id,
        )
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
