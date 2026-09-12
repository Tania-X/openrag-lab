"""Tenant-scoped chat endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from openrag_lab.application.rag.chat_service import ChatService
from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.client import OpenRAGError
from openrag_lab.interfaces.api.deps import (
    CurrentUser,
    DbSession,
    RagGatewayDep,
    require_permission,
)
from openrag_lab.interfaces.schemas.rag import ChatOut, ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatOut)
async def chat(
    body: ChatRequest,
    session: DbSession,
    gateway: RagGatewayDep,
    actor: Annotated[CurrentUser, Depends(require_permission("chat:use"))],
) -> dict:
    """Answer using the documents owned by the caller's tenant."""
    service = ChatService(RetrievalScopeResolver(session), gateway)
    try:
        return await service.chat(
            actor_user_id=actor.user_id,
            actor_tenant_id=actor.tenant_id,
            message=body.message,
            limit=body.limit,
            score_threshold=body.score_threshold,
            tenant_id=body.tenant_id,
        )
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
