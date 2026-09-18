"""Domain tests for the document registry state machine (P1).

The state graph is docs/document-registry-state-design.md §4; these tests pin the
transitions themselves, including the ones that must be **refused** — a state
machine whose illegal edges are not rejected is just a string column.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openrag_lab.domain.identity.models import (
    MAX_STATUS_REASON_LENGTH,
    Document,
)
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.errors import ConflictError, InvalidOperationError
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId


def _document(
    *,
    status: DocumentStatus = DocumentStatus.INDEXED,
    status_reason: str | None = None,
    openrag_document_id: str | None = None,
) -> Document:
    """A registry row; explicit keywords keep mypy able to check the call."""
    return Document(
        id=DocumentId("doc-1"),
        tenant_id=TenantId("t-acme"),
        stored_filename="acme/report.md",
        display_name="report.md",
        uploaded_by=UserId("u-alice"),
        status=status,
        status_reason=status_reason,
        openrag_document_id=openrag_document_id,
    )


def test_a_constructed_document_is_indexed_by_default() -> None:
    """A fully-formed Document describes something usable; upload opts into INDEXING."""
    assert _document().status is DocumentStatus.INDEXED


def test_mark_indexing_from_indexed_and_from_failed() -> None:
    """Both edges into INDEXING are legal: replacement and retry."""
    replacement = _document()
    replacement.mark_indexing()
    assert replacement.status is DocumentStatus.INDEXING

    retry = _document(status=DocumentStatus.FAILED, status_reason="boom")
    retry.mark_indexing()
    assert retry.status is DocumentStatus.INDEXING
    assert retry.status_reason is None, "a retry clears the previous reason"


def test_mark_indexing_while_indexing_is_a_conflict() -> None:
    """Two callers must not both drive the remote state for one name."""
    document = _document(status=DocumentStatus.INDEXING)
    with pytest.raises(ConflictError):
        document.mark_indexing()


def test_mark_indexed_records_the_id_only_when_one_arrives() -> None:
    document = _document(status=DocumentStatus.INDEXING, openrag_document_id="old-id")
    document.mark_indexed("new-id")
    assert document.status is DocumentStatus.INDEXED
    assert document.openrag_document_id == "new-id"

    # A replace-duplicates task need not echo an id; the old one must survive.
    without_id = _document(status=DocumentStatus.INDEXING, openrag_document_id="old-id")
    without_id.mark_indexed(None)
    assert without_id.openrag_document_id == "old-id"


@pytest.mark.parametrize("start", [DocumentStatus.INDEXED, DocumentStatus.FAILED])
def test_promotion_is_only_legal_from_indexing(start: DocumentStatus) -> None:
    """Skipping INDEXING would skip the state that makes a crash discoverable."""
    document = _document(status=start)
    with pytest.raises(InvalidOperationError):
        document.mark_indexed("x")


@pytest.mark.parametrize("start", [DocumentStatus.INDEXED, DocumentStatus.FAILED])
def test_mark_failed_is_only_legal_from_indexing(start: DocumentStatus) -> None:
    document = _document(status=start)
    with pytest.raises(InvalidOperationError):
        document.mark_failed("boom")


def test_mark_failed_records_a_bounded_reason() -> None:
    document = _document(status=DocumentStatus.INDEXING)
    document.mark_failed("x" * (MAX_STATUS_REASON_LENGTH + 50))
    assert document.status is DocumentStatus.FAILED
    assert document.status_reason is not None
    assert len(document.status_reason) == MAX_STATUS_REASON_LENGTH


def test_transitions_bump_updated_at() -> None:
    """The API exposes updated_at, so a status change has to show up there.

    The stored timestamp is aged first: comparing against a freshly constructed
    Document races the clock (two `now()` calls can land in the same tick), which
    made this test flaky.
    """
    aged = datetime(2020, 1, 1, tzinfo=UTC)
    document = _document(status=DocumentStatus.INDEXING)
    document.updated_at = aged

    document.mark_indexed("id")

    assert document.updated_at > aged
