"""Outbound port for retrieval and chat.

Phase 1 keeps the RAG context deliberately thin: the payloads below are
OpenRAG's own response shapes rather than a remodelled domain type. The port
exists so the application layer can be tested without a live OpenRAG, and so
``filters`` (the tenant boundary) is always supplied by openrag-lab instead of
being passed through from a client.

Both methods are blocking, matching the synchronous HTTP client underneath.
Callers running on an event loop must offload them to a worker thread.
"""

from __future__ import annotations

from typing import Any, Protocol


class RagGateway(Protocol):
    """Access to OpenRAG scoped to one tenant's API key."""

    def search(
        self,
        *,
        api_key: str,
        query: str,
        filters: dict[str, Any],
        limit: int,
        score_threshold: float,
        rerank: bool = False,
        rerank_model: str | None = None,
        rerank_top_n: int | None = None,
    ) -> dict[str, Any]: ...

    def chat(
        self,
        *,
        api_key: str,
        message: str,
        filters: dict[str, Any],
        limit: int,
        score_threshold: float,
    ) -> dict[str, Any]: ...
