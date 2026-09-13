"""OpenRAG implementation of the RAG gateway port.

Thin adapter over the existing synchronous ``OpenRAGClient``: it only adds the
per-tenant API key (from the resolved scope) and forwards the server-built
filters. Keeping it here means the application layer never touches httpx.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from openrag_lab.client import OpenRAGClient
from openrag_lab.config import get_settings


class OpenRAGGateway:
    """RagGateway backed by OpenRAG's public v1 API."""

    def __init__(
        self,
        # Deliberately loose: this is the seam tests inject a fake through, and a
        # fake is not an OpenRAGClient subclass. What the client must provide is
        # captured by the calls below, not by this annotation.
        client_factory: Callable[..., Any] = OpenRAGClient,
        base_url: str | None = None,
        ingest_timeout: float | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._base_url = base_url
        self._ingest_timeout = ingest_timeout

    def _client(self, api_key: str) -> Any:
        """Build a client for one tenant.

        The address is passed explicitly rather than left to the client's own
        defaulting, so the gateway always targets the configured OpenRAG
        instance even if that defaulting changes. The ingestion timeout is set
        here too: it decides how long a request thread can be held.
        """
        settings = get_settings()
        base_url = self._base_url or settings.openrag_base_url
        timeout = (
            self._ingest_timeout
            if self._ingest_timeout is not None
            else settings.upload_ingest_timeout_seconds
        )
        return self._client_factory(
            base_url=base_url, api_key=api_key, ingest_timeout=timeout
        )

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

    def ingest_document(
        self,
        *,
        api_key: str,
        stored_filename: str,
        path: Path,
    ) -> dict[str, Any]:
        with self._client(api_key) as client:
            # wait=True: the caller only registers the document once OpenRAG
            # reports the task finished, so the registry never claims a
            # document that failed to index.
            return client.ingest_file(path, wait=True, filename=stored_filename)

    def delete_document(self, *, api_key: str, stored_filename: str) -> dict[str, Any]:
        with self._client(api_key) as client:
            return client.delete_document(stored_filename)

    def find_document_id(self, *, api_key: str, stored_filename: str) -> str | None:
        with self._client(api_key) as client:
            for entry in client.list_files():
                if entry.get("filename") == stored_filename:
                    return str(entry.get("document_id") or "") or None
        return None
