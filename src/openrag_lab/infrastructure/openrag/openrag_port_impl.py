"""OpenRAG implementation of the RAG gateway port.

Thin adapter over the existing synchronous ``OpenRAGClient``: it only adds the
per-tenant API key (from the resolved scope) and forwards the server-built
filters. Keeping it here means the application layer never touches httpx.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openrag_lab.client import OpenRAGClient


class OpenRAGGateway:
    """RagGateway backed by OpenRAG's public v1 API."""

    def __init__(
        self,
        client_factory: Callable[..., OpenRAGClient] = OpenRAGClient,
    ) -> None:
        self._client_factory = client_factory

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
    ) -> dict[str, Any]:
        with self._client_factory(api_key=api_key) as client:
            return client.search(
                query,
                filters=filters,
                limit=limit,
                score_threshold=score_threshold,
                rerank=rerank,
                rerank_model=rerank_model,
                rerank_top_n=rerank_top_n,
            )

    def chat(
        self,
        *,
        api_key: str,
        message: str,
        filters: dict[str, Any],
        limit: int,
        score_threshold: float,
    ) -> dict[str, Any]:
        with self._client_factory(api_key=api_key) as client:
            return client.chat(
                message,
                filters=filters,
                limit=limit,
                score_threshold=score_threshold,
            )
