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

The cache lock only ever guards dictionary work and the borrow counters; client
construction and closing happen outside it (the exception is closing an entry
that nobody holds, which is O(1) and keeps the "evicted" state consistent).

The cache is bounded. Eviction must **not** close a client that a worker thread
is still using, so every entry carries a borrow count: a client is closed when
it is evicted *and* nobody holds it, or (if evicted while in use) the moment the
last borrow ends. Closing on eviction without that count would break in-flight
requests — the 65th tenant would silently kill a request of the oldest one.

Ownership moves to whoever created the gateway: the application closes it during
shutdown (``interfaces.api.deps.close_rag_gateway``), tests close it themselves.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openrag_lab.client import LIST_FILES_MAX, OpenRAGClient, OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.rag.ports import RagOutcomeUnknownError

logger = logging.getLogger(__name__)

#: How many per-tenant clients may stay open at once. Each one keeps a small
#: connection pool alive, so the ceiling bounds sockets rather than memory. A
#: tenant pushed out of the cache is simply rebuilt on its next call.
MAX_CACHED_CLIENTS = 64


def _translate_unknown_outcome(
    exc: OpenRAGError, *, operation: str
) -> Exception:
    """Map a transport-level OpenRAG failure onto the port's "no verdict" error.

    ``OpenRAGClient`` leaves ``status_code`` unset exactly when no HTTP response
    was received (timeout, connection loss) — see its ``_request``. That is the
    observable difference between "OpenRAG answered and refused" and "we never
    found out", and it is the adapter's job to translate it: the application
    must not read adapter-specific error shapes.
    """
    if exc.status_code is None:
        return RagOutcomeUnknownError(str(exc), operation=operation)
    return exc


def _nothing_to_delete(exc: OpenRAGError) -> bool:
    """True when OpenRAG reports that no chunks matched the filename.

    Field-based on purpose, mirroring ``reingest._nothing_to_delete``: matching
    the error wording instead would turn a message change into a false "delete
    failed". A 404 without that shape (a routing mistake, say) stays an error.
    """
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    return (
        exc.status_code == 404
        and payload.get("success") is False
        and int(payload.get("deleted_chunks") or 0) == 0
    )


@dataclass
class _PooledClient:
    """One cached client, plus what the cache needs to close it safely.

    ``refs`` counts in-flight borrowers: a client with ``refs > 0`` is being used
    on a worker thread right now, so eviction may only *mark* it. ``evicted``
    means "no longer in the cache"; whoever releases the last borrow closes it.
    """

    client: Any
    refs: int = 0
    evicted: bool = False


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
        # A ceiling below 1 cannot mean "no cache" here: every borrow needs an
        # entry, and a ceiling of 0 would evict the entry it is about to lend.
        self._max_cached_clients = max(1, max_cached_clients)
        # Insertion order *is* eviction order (dicts keep it), so the oldest key
        # is simply the first one. No LRU touch: touching would mean writing on
        # every call. The price of a wrong guess is real, though — rebuilding a
        # client costs ~12ms of construction plus a fresh handshake — which is
        # why the ceiling is set well above the tenant count this phase expects.
        self._clients: dict[str, _PooledClient] = {}
        self._clients_lock = threading.Lock()

    @contextmanager
    def _borrow(self, api_key: str) -> Iterator[Any]:
        """Lend this tenant's client for the duration of one call.

        The client must not be closed by the caller: it outlives the call, and
        closing it would drop the pool the next call reuses. Returning it is
        enough — leaving the block releases this borrow, which is what lets the
        cache close an evicted client without cutting a request short.
        """
        entry = self._acquire(api_key)
        try:
            yield entry.client
        finally:
            self._release(entry)

    def _acquire(self, api_key: str) -> _PooledClient:
        """Return this tenant's entry, building it on first use, and count us in.

        Lock discipline (评审第 2 轮修订):

        * taking the borrow count happens **under the lock** — otherwise a key
          could be evicted (and closed, because ``refs`` still read 0) between
          the lookup and the increment, which is exactly the in-flight failure
          the count exists to prevent;
        * **construction happens outside the lock**: building an ``httpx.Client``
          costs ~12ms (SSL context, cert loading) and a custom factory may do
          more, so holding the lock there would serialize every tenant's first
          call behind it.

        Building outside means two threads can race on the same cold key. The
        loser's client is closed immediately: one wasted construction is cheaper
        than blocking every other tenant on the lock.
        """
        with self._clients_lock:
            entry = self._clients.get(api_key)
            if entry is not None:
                entry.refs += 1
                return entry

        built = self._build_client(api_key)
        with self._clients_lock:
            entry = self._clients.get(api_key)
            if entry is None:
                entry = _PooledClient(client=built)
                # Count the borrow *before* the entry is visible to eviction.
                # With a ceiling of 0 the new entry is the only candidate for
                # being dropped, and closing it here would hand the caller a
                # closed client (and close it twice on release).
                entry.refs += 1
                self._clients[api_key] = entry
                self._evict_over_ceiling()
            else:
                self._close_client(built)  # lost the race; do not leak it
                entry.refs += 1
            return entry

    def _evict_over_ceiling(self) -> None:
        """Drop oldest entries until the cache fits. Caller holds the lock.

        A dropped entry is only closed when nobody is using it; otherwise it is
        marked and closed by ``_release`` when the last borrow ends. Closing it
        here would fail an in-flight request on that tenant.
        """
        while len(self._clients) > self._max_cached_clients:
            oldest_key = next(iter(self._clients))
            entry = self._clients.pop(oldest_key)
            entry.evicted = True
            if entry.refs == 0:
                self._close_client(entry.client)
                logger.info("Evicted a cached OpenRAG client (cache ceiling reached)")
            else:
                logger.info(
                    "Evicted an in-use OpenRAG client; it closes when the "
                    "current call finishes (cache ceiling reached)"
                )

    def _release(self, entry: _PooledClient) -> None:
        """End one borrow, closing the client if eviction is waiting for it."""
        with self._clients_lock:
            entry.refs -= 1
            if entry.evicted and entry.refs == 0:
                self._close_client(entry.client)

    def _build_client(self, api_key: str) -> Any:
        """Construct one client for ``api_key``.

        The address is passed explicitly rather than left to the client's own
        defaulting, so the gateway always targets the configured OpenRAG
        instance even if that defaulting changes. Both timeouts are set here too:
        the ingest one decides how long a request thread can be held, and the
        per-request one is what reconciliation derives its "stuck" threshold
        from — a budget nothing can configure is a budget nobody can reason about.
        """
        settings = get_settings()
        base_url = self._base_url or settings.openrag_base_url
        ingest_timeout = (
            self._ingest_timeout
            if self._ingest_timeout is not None
            else settings.upload_ingest_timeout_seconds
        )
        return self._client_factory(
            base_url=base_url,
            api_key=api_key,
            timeout=settings.openrag_request_timeout_seconds,
            ingest_timeout=ingest_timeout,
        )

    def close(self) -> None:
        """Close cached clients. Idempotent; safe to call at shutdown.

        A client that a worker thread is still using is marked instead, and
        closed by ``_release`` when that call returns — shutdown must not fail an
        in-flight request either.
        """
        with self._clients_lock:
            entries = list(self._clients.values())
            self._clients.clear()
            for entry in entries:
                entry.evicted = True
                if entry.refs == 0:
                    self._close_client(entry.client)

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
        with self._borrow(api_key) as client:
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
        with self._borrow(api_key) as client:
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
        with self._borrow(api_key) as client:
            # wait=True: the caller only registers the document once OpenRAG
            # reports the task finished, so the registry never claims a
            # document that failed to index.
            try:
                return client.ingest_file(path, wait=True, filename=stored_filename)
            except OpenRAGError as exc:
                raise _translate_unknown_outcome(exc, operation="ingest") from exc

    def delete_document(self, *, api_key: str, stored_filename: str) -> dict[str, Any]:
        with self._borrow(api_key) as client:
            try:
                payload = client.delete_document(stored_filename)
            except OpenRAGError as exc:
                if _nothing_to_delete(exc):
                    # OpenRAG answers "no chunks matched" with 404. Nothing to
                    # remove is a settled delete, not a failure: the caller only
                    # needs to know the name is no longer usable.
                    return {"deleted_chunks": 0, "already_absent": True}
                raise _translate_unknown_outcome(exc, operation="delete") from exc
            if not isinstance(payload, dict):
                # A 2xx with no body still settles the delete; report "removed
                # nothing rather than an unknown count" instead of inventing one.
                return {"deleted_chunks": 0, "already_absent": False}
            return {
                "deleted_chunks": int(payload.get("deleted_chunks") or 0),
                "already_absent": False,
            }

    def list_document_filenames(self, *, api_key: str) -> list[str]:
        """Every stored filename, or an error — see the port contract.

        OpenRAG's listing endpoint stops at ``LIST_FILES_MAX`` and says nothing
        about whether it stopped early, so a full page is treated as *possibly
        truncated* rather than trusted. Reconciliation compares this list as a
        set: a name missing because of paging would be reported as "registered,
        not remote", i.e. a truncation would turn into a screen of false alarms
        on a command built for cron. "Unreadable" is the honest answer here.
        """
        with self._borrow(api_key) as client:
            entries = client.list_files()
            if len(entries) >= LIST_FILES_MAX:
                raise OpenRAGError(
                    f"OpenRAG listing returned {len(entries)} entries, at its "
                    f"{LIST_FILES_MAX}-entry ceiling: the list may be truncated, "
                    "so it cannot be compared against the registry"
                )
            return [
                str(entry["filename"])
                for entry in entries
                if entry.get("filename")
            ]

    def find_document_id(self, *, api_key: str, stored_filename: str) -> str | None:
        with self._borrow(api_key) as client:
            for entry in client.list_files():
                if entry.get("filename") == stored_filename:
                    return str(entry.get("document_id") or "") or None
        return None
