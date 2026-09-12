"""Tenant-scoped document management endpoints.

The tenant boundary is the same one retrieval uses: the caller's own tenant by
default, another tenant only for a ``super_admin``, and always resolved before
anything touches OpenRAG.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from openrag_lab.application.rag.document_service import DocumentService
from openrag_lab.client import OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Document
from openrag_lab.interfaces.api.deps import (
    CurrentUser,
    DbSession,
    RagGatewayDep,
    require_permission,
)
from openrag_lab.interfaces.api.uploads import STAGING_PREFIX
from openrag_lab.interfaces.schemas.documents import (
    DeleteDocumentOut,
    DocumentListOut,
    DocumentOut,
)

router = APIRouter(tags=["documents"])

_CHUNK_BYTES = 1024 * 1024


def _to_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id.value,
        display_name=document.display_name,
        stored_filename=document.stored_filename,
        mimetype=document.mimetype,
        size_bytes=document.size_bytes,
        openrag_document_id=document.openrag_document_id,
        uploaded_by=document.uploaded_by.value,
        created_at=document.created_at.isoformat(),
        updated_at=document.updated_at.isoformat(),
    )


@router.get("/api/documents", response_model=DocumentListOut)
async def list_documents(
    session: DbSession,
    gateway: RagGatewayDep,
    actor: Annotated[CurrentUser, Depends(require_permission("documents:read"))],
    tenant_id: Annotated[str | None, Query()] = None,
) -> DocumentListOut:
    """List the documents the caller's tenant has registered."""
    service = DocumentService(session, gateway)
    documents = await service.list_documents(
        actor_user_id=actor.user_id,
        actor_tenant_id=actor.tenant_id,
        tenant_id=tenant_id,
    )
    return DocumentListOut(total=len(documents), files=[_to_out(d) for d in documents])


@router.post("/api/documents/ingest", response_model=DocumentOut, status_code=201)
async def ingest_document(
    session: DbSession,
    gateway: RagGatewayDep,
    actor: Annotated[CurrentUser, Depends(require_permission("documents:upload"))],
    file: Annotated[UploadFile, File()],
    tenant_id: Annotated[str | None, Form()] = None,
) -> DocumentOut:
    """Store an uploaded file in OpenRAG under the tenant's namespace.

    The document is registered only after OpenRAG reports the ingestion task
    finished, so a failed ingest leaves no registry entry behind.
    """
    settings = get_settings()
    suffix = Path(file.filename or "upload").suffix
    # mkstemp rather than NamedTemporaryFile(delete=True): the gateway reads the
    # file *by path* from a worker thread, and an implicitly-deleting handle
    # cannot be reopened that way on every platform. The file must stay alive
    # until the ingestion task has finished, so its lifetime is explicit here.
    # The prefix makes leftovers from a killed process recognisable; the file is
    # removed in the finally block, and a crash window during the (bounded)
    # ingestion wait is accepted rather than swept.
    descriptor, temp_name = tempfile.mkstemp(prefix=STAGING_PREFIX, suffix=suffix)
    temp_path = Path(temp_name)
    try:
        try:
            buffer_cm = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)  # fdopen failed: nothing owns the fd
            raise
        with buffer_cm as buffer:
            size = 0
            # Stream to disk and stop at the cap rather than buffering the whole
            # upload in memory first.
            while chunk := await file.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File exceeds the upload limit of "
                            f"{settings.max_upload_bytes} bytes"
                        ),
                    )
                buffer.write(chunk)
        service = DocumentService(session, gateway)
        try:
            document = await service.upload_document(
                actor_user_id=actor.user_id,
                actor_tenant_id=actor.tenant_id,
                uploaded_by=actor.user_id,
                filename=file.filename or "",
                path=temp_path,
                mimetype=file.content_type or "application/octet-stream",
                size_bytes=size,
                tenant_id=tenant_id,
            )
        except OpenRAGError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)
        await file.close()
    return _to_out(document)


@router.delete("/api/documents/{filename}", response_model=DeleteDocumentOut)
async def delete_document(
    filename: str,
    session: DbSession,
    gateway: RagGatewayDep,
    actor: Annotated[CurrentUser, Depends(require_permission("documents:delete"))],
    # Declared explicitly: an unannotated parameter is still inferred as a query
    # parameter today, but the contract should not depend on that inference.
    tenant_id: Annotated[str | None, Query()] = None,
) -> DeleteDocumentOut:
    """Delete a registered document by its display name."""
    service = DocumentService(session, gateway)
    try:
        result = await service.delete_document(
            actor_user_id=actor.user_id,
            actor_tenant_id=actor.tenant_id,
            filename=filename,
            tenant_id=tenant_id,
        )
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DeleteDocumentOut(**result)
