"""OpenRAG implementation of the RAG gateway port.

Thin adapter over the existing synchronous ``OpenRAGClient``: it only adds the
per-tenant API key (from the resolved scope) and forwards the server-built
filters. Keeping it here means the application layer never touches httpx.

Clients are **cached per API key** rather than built per call. Two reasons:

* a fresh ``OpenRAGClient`` means a fresh ``httpx.Client`` — a new TCP/TLS
  handshake and connection pool for every search, chat and upload;
* the key is bound when the client is constructed (``OpenRAGClient`` stores it
  and only formats it into a header later), so "one client for the process"
  would mix tenants. The cache key is therefore the key itself.

The cache is bounded, and evicting an entry closes the client it drops.
Ownership moves to whoever created the gateway: the application closes it during
shutdown (``interfaces.api.deps.close_rag_gateway``), tests close it themselves.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openrag_lab.client import OpenRAGClient
from openrag_lab.config import get_settings

logger = logging.getLogger(__name__)

#: How many per-tenant clients may stay open at once. Each one keeps a small
#: connection pool alive, so the ceiling bounds sockets rather than memory. A
#: tenant pushed out of the cache is simply rebuilt on its next call.
MAX_CACHED_CLIENTS = 64


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
        max_cached_clients: int = MAX_CACHED_CLIENTS,
    ) -> None:
        self._client_factory = client_factory
        self._base_url = base_url
        self._ingest_timeout = ingest_timeout
        self._max_cached_clients = max_cached_clients
        # Insertion order *is* eviction order (dicts keep it), so the oldest key
        # is simply the first one. No LRU touch: a hot tenant that gets evicted
        # is rebuilt for one handshake, whereas touching on every call would
        # mean taking a lock on the hot path.
        self._clients: dict[str, Any] = {}
        self._clients_lock = threading.Lock()

    def _client(self, api_key: str) -> Any:
        """Return this tenant's client, building it on first use.

        Callers must **not** close what they get back: the client outlives the
        call, and closing it would drop the pool the next call wants to reuse.

        Thread safety: gateway calls run on anyio worker threads, so two calls
        for the same cold key can race. The lock plus a second look at the cache
        keeps that to exactly one client per key.
        """
        cached = self._clients.get(api_key)
        if cached is not None:
            return cached

        evicted: Any = None
        with self._clients_lock:
            cached = self._clients.get(api_key)  # another thread may have won
            if cached is None:
                cached = self._build_client(api_key)
                self._clients[api_key] = cached
                if len(self._clients) > self._max_cached_clients:
                    oldest_key = next(iter(self._clients))
                    evicted = self._clients.pop(oldest_key)
        if evicted is not None:
            # Drop the pool outside the lock: other tenants should not wait on
            # it, and the entry is already out of the cache.
            self._close_client(evicted)
            logger.info("Evicted a cached OpenRAG client (cache ceiling reached)")
        return cached

    def _build_client(self, api_key: str) -> Any:
        """Construct one client for ``api_key``.

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

    def close(self) -> None:
        """Close every cached client. Idempotent; safe to call at shutdown."""
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            self._close_client(client)

    @staticmethod
    def _close_client(client: Any) -> None:
        """Close one client, never letting a failure escape.

        Shutdown and eviction must not be derailed by one broken socket: the
        remaining clients still have to be released.
        """
        try:
            client.close()
        except Exception:  # noqa: BLE001 - cleanup is best effort by design
            logger.warning("Failed to close an OpenRAG client", exc_info=True)

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
        client = self._client(api_key)
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
        client = self._client(api_key)
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
        client = self._client(api_key)
        # wait=True: the caller only registers the document once OpenRAG
        # reports the task finished, so the registry never claims a
        # document that failed to index.
        return client.ingest_file(path, wait=True, filename=stored_filename)

    def delete_document(self, *, api_key: str, stored_filename: str) -> dict[str, Any]:
        client = self._client(api_key)
        return client.delete_document(stored_filename)

    def find_document_id(self, *, api_key: str, stored_filename: str) -> str | None:
        client = self._client(api_key)
        for entry in client.list_files():
            if entry.get("filename") == stored_filename:
                return str(entry.get("document_id") or "") or None
        return None
