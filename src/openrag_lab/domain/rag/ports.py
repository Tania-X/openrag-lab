"""Outbound port for retrieval, chat and the document store.

Phase 1 keeps the RAG context deliberately thin: the payloads below are
OpenRAG's own response shapes rather than a remodelled domain type. The port
exists so the application layer can be tested without a live OpenRAG, and so
``filters`` (the tenant boundary) is always supplied by openrag-lab instead of
being passed through from a client.

Every method is blocking, matching the synchronous HTTP client underneath.
Callers running on an event loop must offload them to a worker thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RagOutcomeUnknownError(RuntimeError):
    """A gateway call ended without a verdict from OpenRAG.

    Raised for transport-level failures (timeout, connection lost): the request
    may have been applied remotely, may still be running, or may never have
    arrived. The distinction from an ordinary failure is the whole point —
    "OpenRAG refused" and "we do not know" call for different recovery, and
    collapsing them is how a registry ends up asserting something it cannot
    support.

    Part of the port contract, so adapters raise it and the application never
    has to interpret adapter-specific error shapes.
    """

    def __init__(self, message: str, *, operation: str = "") -> None:
        super().__init__(message)
        #: Which port method lost the verdict ("ingest"/"delete").
        self.operation = operation


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

    def ingest_document(
        self,
        *,
        api_key: str,
        stored_filename: str,
        path: Path,
    ) -> dict[str, Any]:
        """Store ``path`` in OpenRAG under the tenant-namespaced name.

        Returns OpenRAG's final ingestion task payload. Raises
        :class:`RagOutcomeUnknownError` when no verdict was received — the
        caller must not treat that as "nothing was written".
        """
        ...

    def delete_document(self, *, api_key: str, stored_filename: str) -> dict[str, Any]:
        """Remove every chunk stored under ``stored_filename``.

        Returns ``{"deleted_chunks": int, "already_absent": bool}``. A normal
        return means the outcome is **known**: either chunks were removed or
        OpenRAG reported that nothing matched the name (``already_absent``) —
        both make the name unusable for retrieval, so both are a settled delete.
        Turning the 404 into a normal return keeps OpenRAG's status-code
        convention inside the adapter, where it belongs.

        Raises :class:`RagOutcomeUnknownError` when the outcome is unknown, and
        the underlying error otherwise (a real refusal: the delete did not
        happen).
        """
        ...

    def find_document_id(self, *, api_key: str, stored_filename: str) -> str | None:
        """Return the id OpenRAG assigned to ``stored_filename``, if known.

        Ingestion tasks do not report it, so it has to be looked up. Best
        effort: ``None`` when the document cannot be located (for example when
        the listing endpoint truncates a large library).
        """
        ...
