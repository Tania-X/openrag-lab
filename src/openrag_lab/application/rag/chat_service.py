"""Chat scoped to the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Any

from anyio import to_thread

from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.domain.rag.ports import RagGateway


class ChatService:
    """Run a tenant-scoped chat turn against OpenRAG."""

    def __init__(self, resolver: RetrievalScopeResolver, gateway: RagGateway) -> None:
        self._resolver = resolver
        self._gateway = gateway

    async def chat(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        message: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        scope = await self._resolver.resolve(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=tenant_id,
        )
        payload = await to_thread.run_sync(
            partial(
                self._gateway.chat,
                api_key=scope.api_key,
                message=message,
                filters=scope.filters,
                limit=limit,
                score_threshold=score_threshold,
            )
        )
        return {
            "response": payload.get("response"),
            "scope": scope.describe(),
        }
