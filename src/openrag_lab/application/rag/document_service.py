"""Document management scoped to the caller's tenant.

Four rules shape this service:

* the *stored* filename is always ``<tenant namespace><name>``, derived by the
  domain (``Tenant.scope_filename``) rather than trusted from the client;
* **intent first**: the registry row is written (``INDEXING``) and committed
  *before* OpenRAG is called, so a crash at any later point leaves a row that
  reconciliation can find — previously the worst case was a remote document with
  no local row at all, which nothing could see or delete
  (docs/document-registry-state-design.md §5);
* a row only becomes ``INDEXED`` (the only status that enters a retrieval scope)
  after OpenRAG reports a finished ingestion task, and a failure marks it
  ``FAILED`` with a reason instead of leaving only a log line;
* deleting works from the registry outwards, so only documents a tenant has
  registered can be deleted — and it keeps a ``DELETED`` tombstone instead of
  dropping the row, so a delete that failed remotely can still be finished
  later (design §5.2).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.domain.identity.models import Document, Tenant
from openrag_lab.domain.rag.documents import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    is_supported_document,
)
from openrag_lab.domain.rag.ports import RagGateway, RagOutcomeUnknownError
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.errors import InvalidOperationError, NotFoundError
from openrag_lab.domain.shared.ids import DocumentId, UserId
from openrag_lab.infrastructure.db.repositories.identity import SqlDocumentRepository
from openrag_lab.infrastructure.openrag.tenant_scope import resolve_tenant_scope

logger = logging.getLogger(__name__)

#: How many uploads/deletes may hold a worker thread at once.
#:
#: Every gateway call is blocking, so it runs in an anyio worker thread. The
#: default pool is 40 threads and is shared by *all* offloaded calls, while an
#: upload can hold its thread for the whole ingest timeout (300s by default).
#: Without a separate ceiling a burst of uploads parks the pool and the
#: millisecond-scale searches queue behind them.
#:
#: Mutations get their own limiter; reads (search, chat, document id lookup)
#: stay on the default pool. The worst case then degrades from "everything
#: queues behind uploads" to "uploads queue behind each other".
INGEST_MAX_CONCURRENCY = 8

# Built at import time on purpose. Review asked whether a module-level limiter is
# safe across event loops (tests call anyio.run several times); measured on anyio
# 4.14 it is — the primitives are backend-agnostic there, and a limiter shared
# across sequential loops (contended, with waiters) works. Loop binding was an
# anyio 3 concern, so a downgrade below 4 would need this revisited.
_INGEST_LIMITER = anyio.CapacityLimiter(INGEST_MAX_CONCURRENCY)


def basename_for_storage(raw_filename: str) -> str:
    """Reduce a client-supplied filename to a plain basename.

    Browsers send full paths in multipart filenames (``C:\\fakepath\\x.pdf``),
    and other clients send relative ones (``../../x.pdf``). Reducing them here,
    at the boundary, keeps the domain rule strict: ``Tenant.scope_filename``
    rejects anything that is not already a plain name.
    """
    cleaned = (raw_filename or "").strip().replace("\\", "/")
    return cleaned.rsplit("/", maxsplit=1)[-1]


class DocumentService:
    """List, add and remove the documents a tenant owns."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: RagGateway,
        resolver: RetrievalScopeResolver | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._resolver = resolver or RetrievalScopeResolver(session)
        self._documents = SqlDocumentRepository(session)

    async def list_documents(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        tenant_id: str | None = None,
    ) -> list[Document]:
        tenant, _ = await self._resolver.resolve_tenant(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=tenant_id,
        )
        return await self._documents.list_by_tenant(tenant.id)

    async def upload_document(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        uploaded_by: str,
        filename: str,
        path: Path,
        mimetype: str = "application/octet-stream",
        size_bytes: int = 0,
        tenant_id: str | None = None,
    ) -> Document:
        tenant, _ = await self._resolver.resolve_tenant(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=tenant_id,
        )
        display_name = basename_for_storage(filename)
        stored_filename = tenant.scope_filename(display_name)  # raises on invalid names
        if not is_supported_document(display_name):
            # Same list the CLI ingestion uses: a format that path skips must
            # not slip into OpenRAG's parsers through the API.
            raise InvalidOperationError(
                f"Unsupported file type: {display_name} "
                f"(accepted: {', '.join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))})"
            )
        api_key = resolve_tenant_scope(tenant).api_key

        existing = await self._documents.find_by_stored_filename(tenant.id, stored_filename)
        if existing is None:
            document = Document(
                id=DocumentId.generate(),
                tenant_id=tenant.id,
                stored_filename=stored_filename,
                display_name=display_name,
                uploaded_by=UserId(uploaded_by),
                mimetype=mimetype,
                size_bytes=size_bytes,
                status=DocumentStatus.INDEXING,
            )
        else:
            # Same name, same tenant: replace (was INDEXED) or retry (was FAILED).
            # Still INDEXING means another upload owns this name right now, and
            # mark_indexing() turns that into a ConflictError (409) rather than
            # letting two callers drive the remote state.
            document = existing
            document.display_name = display_name
            document.mimetype = mimetype
            document.size_bytes = size_bytes
            document.uploaded_by = UserId(uploaded_by)
            document.mark_indexing()

        # Intent first (design §5.1): this commit is what makes a crash
        # discoverable — the row exists and says INDEXING, so reconciliation can
        # ask OpenRAG what actually happened.
        await self._commit_registry(
            action="intent",
            tenant_label=tenant.slug,
            stored_filename=stored_filename,
            save=lambda: self._documents.save(document),
        )

        try:
            task = await to_thread.run_sync(
                lambda: self._gateway.ingest_document(
                    api_key=api_key,
                    stored_filename=stored_filename,
                    path=path,
                ),
                limiter=_INGEST_LIMITER,
            )
            _ensure_ingested(task, stored_filename)
            # The task payload does not carry the document id, so read it back
            # once the document exists. Best effort: a miss leaves the field
            # untouched.
            resolved_id = await to_thread.run_sync(
                lambda: self._gateway.find_document_id(
                    api_key=api_key, stored_filename=stored_filename
                )
            )
        except Exception as exc:
            # Record the failure on the row and re-raise: the caller still gets
            # its 502, but the registry now shows what happened.
            await self._mark_failed(document, tenant, stored_filename, exc)
            raise

        document.mark_indexed(resolved_id)
        await self._commit_registry(
            action="promote",
            tenant_label=tenant.slug,
            stored_filename=stored_filename,
            save=lambda: self._documents.save(document),
        )
        return document

    async def _mark_failed(
        self,
        document: Document,
        tenant: Tenant,
        stored_filename: str,
        exc: BaseException,
    ) -> None:
        """Record a failed ingestion on the row, without masking the failure.

        If even this write fails the row stays ``INDEXING`` — also a
        non-terminal state, so the crash path is still discoverable (design §5.1).

        A timeout is recorded as an *unknown* outcome, not as a clean failure:
        OpenRAG may have written the document before it stopped answering, and
        the difference decides whether reconciliation has to probe at all.
        """
        prefix = (
            "no verdict from OpenRAG"
            if isinstance(exc, RagOutcomeUnknownError)
            else type(exc).__name__
        )
        document.mark_failed(
            f"{prefix}: {type(exc).__name__}: {exc}",
            outcome_unknown=isinstance(exc, RagOutcomeUnknownError),
        )
        try:
            await self._commit_registry(
                action="fail",
                tenant_label=tenant.slug,
                stored_filename=stored_filename,
                save=lambda: self._documents.save(document),
            )
        except Exception:  # noqa: BLE001 - the original failure must win
            logger.exception(
                "Could not record the failure for %s; the row stays INDEXING "
                "and reconciliation can still see it",
                stored_filename,
            )

    async def delete_document(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        filename: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove a registered document from OpenRAG, keeping a tombstone.

        Scope comes from :meth:`RetrievalScopeResolver.resolve_tenant`, exactly
        as for search: a caller reaches its own tenant, and a global
        ``super_admin`` may pass ``tenant_id`` to act on any active tenant
        (that is the same authority it uses to create users elsewhere — see the
        authorization model documented in retrieval_scope). Everyone else is
        rejected with 403 by the resolver.

        The registry row is a hand-rolled finalizer (design §5.2): it moves to
        ``DELETING`` — committed *before* OpenRAG is called — and ends as a
        ``DELETED`` tombstone instead of disappearing. Dropping the row would
        destroy the only record that this name was ever registered here, which
        is precisely what makes a remote leftover unreachable through the API.
        """
        tenant, _ = await self._resolver.resolve_tenant(
            actor_user_id=actor_user_id,
            actor_tenant_id=actor_tenant_id,
            requested_tenant_id=tenant_id,
        )
        # Delete deliberately does *not* reduce the name to a basename: doing so
        # turned a malformed request (``a\b.md``, ``" b.md"``) into a delete of a
        # different document in the caller's own tenant. A name that is not a
        # plain filename is rejected instead.
        display_name = filename
        stored_filename = tenant.scope_filename(display_name)

        document = await self._documents.find_by_stored_filename(
            tenant.id, stored_filename
        )
        if document is None:
            # Deleting by name cannot reach anything the tenant did not
            # register, so an unknown name is simply not found.
            raise NotFoundError(f"Document not found: {display_name}")
        if document.status is DocumentStatus.DELETED:
            # The tombstone is bookkeeping, not a document: from the caller's
            # side the name is gone, and re-deleting cannot reach anything.
            # Retrying an *unconfirmed* delete is reconciliation's job (P3) —
            # keeping the retry out of the request path is what stops a dead
            # request from being the only thing that ever repairs the row.
            raise NotFoundError(f"Document not found: {display_name}")
        # INDEXING (an upload is in flight) and DELETING (another delete is
        # already driving the remote) both mean "this name is busy": 409.
        document.mark_deleting()

        # Intent first, exactly as for uploads: if the process dies after this
        # commit, the row says DELETING and reconciliation can finish the job.
        await self._commit_registry(
            action="delete-intent",
            tenant_label=tenant.slug,
            stored_filename=stored_filename,
            save=lambda: self._documents.save(document),
        )

        try:
            result = await to_thread.run_sync(
                lambda: self._gateway.delete_document(
                    api_key=resolve_tenant_scope(tenant).api_key,
                    stored_filename=stored_filename,
                ),
                limiter=_INGEST_LIMITER,
            )
        except RagOutcomeUnknownError as exc:
            # OpenRAG never gave a verdict. The tenant-visible effect already
            # holds (the name left the retrieval boundary when the row left
            # INDEXED), so this counts as a delete — but it is recorded as
            # unconfirmed so reconciliation can verify instead of trusting it.
            await self._mark_deleted(
                document,
                tenant,
                stored_filename,
                confirmed=False,
                detail=f"no verdict from OpenRAG: {type(exc).__name__}: {exc}",
            )
            return {
                "filename": display_name,
                "stored_filename": stored_filename,
                "deleted_chunks": 0,
                "confirmed": False,
            }
        except Exception as exc:
            # A real refusal (OpenRAG answered and said no): nothing was removed.
            # The row stays DELETING — a non-terminal state, so the delete is
            # discoverable and retryable — and it records *why* it is stuck, the
            # same way a failed upload does. Then the error keeps going: the
            # caller still gets its 502, and the state does not move (recording a
            # failure is not progress, and pretending otherwise would hide the
            # retry that reconciliation owes).
            await self._note_delete_failure(document, tenant, stored_filename, exc)
            raise

        chunks = int(result.get("deleted_chunks") or 0)
        detail = (
            "OpenRAG had no chunks for this name"
            if result.get("already_absent")
            else f"removed {chunks} chunk(s)"
        )
        await self._mark_deleted(
            document,
            tenant,
            stored_filename,
            confirmed=True,
            detail=detail,
        )
        return {
            "filename": display_name,
            "stored_filename": stored_filename,
            "deleted_chunks": chunks,
            "confirmed": True,
        }

    async def _note_delete_failure(
        self,
        document: Document,
        tenant: Tenant,
        stored_filename: str,
        exc: BaseException,
    ) -> None:
        """Record a refused delete on the row, without masking the refusal.

        If even this write fails, the row stays ``DELETING`` with no reason —
        still a non-terminal, discoverable state, so the retry is not lost.
        """
        document.note_delete_failure(f"{type(exc).__name__}: {exc}")
        try:
            await self._commit_registry(
                action="delete-refused",
                tenant_label=tenant.slug,
                stored_filename=stored_filename,
                save=lambda: self._documents.save(document),
            )
        except Exception:  # noqa: BLE001 - the original refusal must win
            logger.exception(
                "Could not record the delete refusal for %s; the row stays "
                "DELETING and reconciliation can still retry it",
                stored_filename,
            )

    async def _mark_deleted(
        self,
        document: Document,
        tenant: Tenant,
        stored_filename: str,
        *,
        confirmed: bool,
        detail: str,
    ) -> None:
        """Close the delete out on the row, without masking a failure to record it.

        If this write fails the row stays ``DELETING`` — also non-terminal, so
        the crash path is still discoverable. The caller must not turn that into
        a "delete failed" answer: OpenRAG's side is settled either way, and
        reporting a failure here would invite a retry that cannot help.
        """
        document.mark_deleted(confirmed=confirmed, detail=detail)
        try:
            await self._commit_registry(
                action="delete-done",
                tenant_label=tenant.slug,
                stored_filename=stored_filename,
                save=lambda: self._documents.save(document),
            )
        except Exception:  # noqa: BLE001 - the delete outcome must win
            logger.exception(
                "Could not record the delete outcome for %s; the row stays "
                "DELETING and reconciliation can still finish it",
                stored_filename,
            )

    async def _commit_registry(
        self,
        *,
        action: str,
        tenant_label: str,
        stored_filename: str,
        save: Callable[[], Awaitable[None]],
    ) -> None:
        """Persist a registry change, logging loudly if it fails.

        OpenRAG has already been changed by the time this runs, so a failure
        here leaves the two sides inconsistent (an unregistered document in
        OpenRAG, or a registry row pointing at nothing). The write is rolled
        back and the mismatch logged with enough context to repair it by hand.
        """
        try:
            await save()
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.error(
                "Registry %s failed after OpenRAG was already changed: "
                "tenant=%s stored=%s",
                action,
                tenant_label,
                stored_filename,
            )
            raise


def _ensure_ingested(task: dict[str, Any], stored_filename: str) -> None:
    """Raise unless OpenRAG reports the document as ingested."""
    status = str(task.get("status") or "")
    failed = int(task.get("failed_files") or 0)
    if status == "completed" and failed == 0:
        return
    detail = task.get("error") or task.get("message") or task.get("failed_files")
    raise InvalidOperationError(
        f"Ingestion did not complete for {stored_filename}: status={status or 'unknown'} "
        f"failed_files={failed} detail={detail}"
    )
