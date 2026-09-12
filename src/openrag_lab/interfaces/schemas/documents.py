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
    created_at: str
    updated_at: str


class DocumentListOut(BaseModel):
    total: int
    files: list[DocumentOut]


class DeleteDocumentOut(BaseModel):
    filename: str
    stored_filename: str
    deleted_chunks: int
