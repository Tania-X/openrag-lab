"""OpenRAG implementation of the RAG gateway port.

Thin adapter over the existing synchronous ``OpenRAGClient``: it only adds the
per-tenant API key (from the resolved scope) and forwards the server-built
filters. Keeping it here means the application layer never touches httpx.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openrag_lab.client import OpenRAGClient
from openrag_lab.config import get_settings


class OpenRAGGateway:
    """RagGateway backed by OpenRAG's public v1 API."""

    def __init__(
        self,
        client_factory: Callable[..., OpenRAGClient] = OpenRAGClient,
        base_url: str | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._base_url = base_url

    def _client(self, api_key: str) -> OpenRAGClient:
        """Build a client for one tenant.

        The address is passed explicitly rather than left to the client's own
        defaulting, so the gateway always targets the configured OpenRAG
        instance even if that defaulting changes.
        """
        base_url = self._base_url or get_settings().openrag_base_url
        return self._client_factory(base_url=base_url, api_key=api_key)

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
        with self._client(api_key) as client:
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
        with self._client(api_key) as client:
            return client.chat(
                message,
                filters=filters,
                limit=limit,
                score_threshold=score_threshold,
            )
