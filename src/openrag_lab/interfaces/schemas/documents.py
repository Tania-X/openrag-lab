"""Request/response contracts for document management."""

from __future__ import annotations

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    display_name: str
    stored_filename: str
    mimetype: str
    size_bytes: int
    openrag_document_id: str | None = None
    uploaded_by: str
    #: Registry lifecycle (indexing / indexed / failed). Only `indexed` documents
    #: take part in retrieval, so the client needs this to explain why a listed
    #: document is not searchable yet. `status_reason` stays internal: it quotes
    #: upstream error text.
    status: str
    created_at: str
    updated_at: str


class DocumentListOut(BaseModel):
    total: int
    files: list[DocumentOut]


class DeleteDocumentOut(BaseModel):
    filename: str
    stored_filename: str
    deleted_chunks: int
    #: Whether OpenRAG actually gave a verdict for this delete. ``false`` means
    #: the outcome is unknown (timeout/no response): the document is out of the
    #: retrieval boundary either way, but it may still occupy space remotely
    #: until reconciliation verifies it. Reporting this matters — answering a
    #: bare "deleted" would let a guess pass as a fact.
    confirmed: bool = True
