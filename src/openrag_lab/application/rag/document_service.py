"""Document management scoped to the caller's tenant.

Three rules shape this service:

* the *stored* filename is always ``<tenant namespace><name>``, derived by the
  domain (``Tenant.scope_filename``) rather than trusted from the client;
* a document only enters the registry after OpenRAG reports a finished
  ingestion task, so the registry never claims something that is not indexed;
* deleting works from the registry outwards, so only documents a tenant has
  registered can be deleted.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from openrag_lab.application.rag.retrieval_scope import RetrievalScopeResolver
from openrag_lab.domain.identity.models import Document
from openrag_lab.domain.rag.documents import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    is_supported_document,
)
from openrag_lab.domain.rag.ports import RagGateway
from openrag_lab.domain.shared.errors import InvalidOperationError, NotFoundError
from openrag_lab.domain.shared.ids import DocumentId, UserId
from openrag_lab.infrastructure.db.repositories.identity import SqlDocumentRepository
from openrag_lab.infrastructure.openrag.tenant_scope import resolve_tenant_scope

logger = logging.getLogger(__name__)


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

        task = await to_thread.run_sync(
            lambda: self._gateway.ingest_document(
                api_key=api_key,
                stored_filename=stored_filename,
                path=path,
            )
        )
        _ensure_ingested(task, stored_filename)
        # The task payload does not carry the document id, so read it back once
        # the document exists. Best effort: a miss leaves the field untouched.
        resolved_id = await to_thread.run_sync(
            lambda: self._gateway.find_document_id(
                api_key=api_key, stored_filename=stored_filename
            )
        )

        existing = await self._documents.find_by_stored_filename(tenant.id, stored_filename)
        if existing is not None:
            # Re-upload replaces: OpenRAG was told replace_duplicates=true, so
            # the stored document is already the new content.
            existing.display_name = display_name
            existing.mimetype = mimetype
            existing.size_bytes = size_bytes
            existing.uploaded_by = UserId(uploaded_by)
            # Only overwrite an id we actually received: a replace-duplicates
            # task need not echo one, and losing it would be a silent downgrade.
            new_id = resolved_id
            if new_id is not None:
                existing.openrag_document_id = new_id
            # A replacement changes the record, so surface it: updated_at is
            # exposed by the API and would otherwise stay equal to created_at.
            existing.touch()
            document = existing
        else:
            document = Document(
                id=DocumentId.generate(),
                tenant_id=tenant.id,
                stored_filename=stored_filename,
                display_name=display_name,
                uploaded_by=UserId(uploaded_by),
                mimetype=mimetype,
                size_bytes=size_bytes,
                openrag_document_id=resolved_id,
            )
        await self._commit_registry(
            action="register",
            tenant_label=tenant.slug,
            stored_filename=stored_filename,
            save=lambda: self._documents.save(document),
        )
        return document

    async def delete_document(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        filename: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove a registered document from OpenRAG and the registry.

        Scope comes from :meth:`RetrievalScopeResolver.resolve_tenant`, exactly
        as for search: a caller reaches its own tenant, and a global
        ``super_admin`` may pass ``tenant_id`` to act on any active tenant
        (that is the same authority it uses to create users elsewhere — see the
        authorization model documented in retrieval_scope). Everyone else is
        rejected with 403 by the resolver.
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

        result = await to_thread.run_sync(
            lambda: self._gateway.delete_document(
                api_key=resolve_tenant_scope(tenant).api_key,
                stored_filename=stored_filename,
            )
        )
        await self._commit_registry(
            action="remove",
            tenant_label=tenant.slug,
            stored_filename=stored_filename,
            save=lambda: self._documents.delete(document.id),
        )
        return {
            "filename": display_name,
            "stored_filename": stored_filename,
            "deleted_chunks": int(result.get("deleted_chunks") or 0),
        }

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
