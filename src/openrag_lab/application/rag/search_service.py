"""Semantic search scoped to the caller's tenant."""

from __future__ import annotations

from functools import partial
from typing import Any

from anyio import to_thread

from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.domain.rag.ports import RagGateway


class SearchService:
    """Run a tenant-scoped search against OpenRAG."""

    def __init__(self, resolver: RetrievalScopeResolver, gateway: RagGateway) -> None:
        self._resolver = resolver
        self._gateway = gateway

    async def search(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        rerank: bool = False,
        rerank_model: str | None = None,
        rerank_top_n: int | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        scope = await self._resolver.resolve(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=tenant_id,
        )
        # The gateway is a blocking HTTP client, so keep the event loop free.
        payload = await to_thread.run_sync(
            partial(
                self._gateway.search,
                api_key=scope.api_key,
                query=query,
                filters=scope.filters,
                limit=limit,
                score_threshold=score_threshold,
                rerank=rerank,
                rerank_model=rerank_model,
                rerank_top_n=rerank_top_n,
            )
        )
        return {
            "results": payload.get("results", []),
            "scope": scope.describe(),
        }


__all__ = ["SearchService", "RagGateway"]
