"""Domain tests for the document registry state machine (P1 + P2).

The state graph is docs/document-registry-state-design.md §4; these tests pin the
transitions themselves, including the ones that must be **refused** — a state
machine whose illegal edges are not rejected is just a string column.

P2 adds the delete side (``DELETING`` -> ``DELETED``) with the tombstone rule:
the row survives the delete, and the row records whether OpenRAG confirmed it.
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
    remote_outcome_unknown: bool = False,
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
        remote_outcome_unknown=remote_outcome_unknown,
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


# ── P2: the delete side ────────────────────────────────────────────────────


@pytest.mark.parametrize("start", [DocumentStatus.INDEXED, DocumentStatus.FAILED])
def test_mark_deleting_from_indexed_and_from_failed(start: DocumentStatus) -> None:
    """Both live states can be deleted; FAILED rows must not be stuck."""
    document = _document(status=start, status_reason="boom")
    document.mark_deleting()
    assert document.status is DocumentStatus.DELETING
    assert document.status_reason is None, "the new operation clears the old reason"


@pytest.mark.parametrize(
    "start", [DocumentStatus.INDEXING, DocumentStatus.DELETING, DocumentStatus.DELETED]
)
def test_mark_deleting_is_refused_while_the_name_is_busy_or_gone(
    start: DocumentStatus,
) -> None:
    """INDEXING/DELETING: another operation owns the name → conflict.

    DELETED: there is nothing left to delete. The service answers 404 before it
    gets here (a tombstone is not a document), so reaching this means a caller
    bypassed that check — still a conflict, never a second remote delete.
    """
    document = _document(status=start)
    with pytest.raises(ConflictError):
        document.mark_deleting()


def test_mark_indexing_is_refused_while_a_delete_is_in_flight() -> None:
    """The other half of the same rule: an upload must not race a delete.

    This is the upload-vs-delete conflict the API answers with 409 — letting it
    through would let one caller's ingest overwrite the other's removal.
    """
    document = _document(status=DocumentStatus.DELETING)
    with pytest.raises(ConflictError):
        document.mark_indexing()


def test_mark_deleted_records_a_confirmed_removal() -> None:
    document = _document(status=DocumentStatus.DELETING)
    document.mark_deleted(confirmed=True, detail="removed 3 chunk(s)")

    assert document.status is DocumentStatus.DELETED
    assert document.status_reason == "removed 3 chunk(s)"
    assert document.remote_outcome_unknown is False


def test_mark_deleted_without_a_verdict_is_flagged_as_unknown() -> None:
    """A timeout still deletes (the name left the retrieval boundary), but the
    row must say the outcome was never confirmed — that flag is the worklist
    reconciliation reads, and prose in status_reason is not a substitute."""
    document = _document(status=DocumentStatus.DELETING)
    document.mark_deleted(confirmed=False, detail="no verdict from OpenRAG: Timeout")

    assert document.status is DocumentStatus.DELETED
    assert document.remote_outcome_unknown is True


@pytest.mark.parametrize(
    "start",
    [DocumentStatus.INDEXED, DocumentStatus.FAILED, DocumentStatus.INDEXING],
)
def test_mark_deleted_is_only_legal_from_deleting(start: DocumentStatus) -> None:
    """Concluding a delete that never started would record a removal OpenRAG was
    never asked to perform."""
    document = _document(status=start)
    with pytest.raises(InvalidOperationError):
        document.mark_deleted(confirmed=True, detail="nope")


def test_a_deleted_name_can_be_re_uploaded_on_the_same_row() -> None:
    """Resurrection: the tombstone keeps the key, so a re-upload reuses the row.

    Keeping the row is what makes this work — a hard delete would have to
    re-create the identity, and `(tenant_id, stored_filename)` is unique.
    """
    document = _document(
        status=DocumentStatus.DELETED,
        status_reason="no verdict from OpenRAG: Timeout",
        remote_outcome_unknown=True,
    )
    document.mark_indexing()

    assert document.status is DocumentStatus.INDEXING
    assert document.status_reason is None
    assert document.remote_outcome_unknown is False, "the retry is not a stale unknown"


def test_marking_failed_can_record_that_openrag_never_answered() -> None:
    """An ingest timeout means the document may exist remotely; the row has to
    say so, or reconciliation cannot tell it apart from a clean refusal."""
    clean = _document(status=DocumentStatus.INDEXING)
    clean.mark_failed("InvalidOperationError: status=failed")
    assert clean.remote_outcome_unknown is False

    timed_out = _document(status=DocumentStatus.INDEXING)
    timed_out.mark_failed("no verdict from OpenRAG: Timeout", outcome_unknown=True)
    assert timed_out.status is DocumentStatus.FAILED
    assert timed_out.remote_outcome_unknown is True


def test_promotion_clears_a_stale_unknown_flag() -> None:
    """A row that ends up INDEXED has a verdict by definition."""
    document = _document(status=DocumentStatus.INDEXING, remote_outcome_unknown=True)
    document.mark_indexed("id")
    assert document.remote_outcome_unknown is False


def test_deleting_voids_the_remote_document_id() -> None:
    """删除意图那一刻, id 就该作废 —— 它声称的是"远端存在这份文档"。

    留着它, 墓碑(以及复活后的行)就会一直挂着一个远端已经不存在的 id, 而它在
    API 响应里是可见的。复活重传若这次查不到新 id, 旧 id 也不会被覆盖
    (mark_indexed 只在真的收到 id 时才覆盖), 于是假事实会长期留在库里。
    """
    document = _document(status=DocumentStatus.INDEXED, openrag_document_id="orag-old")
    document.mark_deleting()
    assert document.openrag_document_id is None


def test_a_replacement_upload_still_keeps_the_id() -> None:
    """反向守卫: 替换上传不能清 id(那是静默降级, 见 mark_indexed 的注释)。

    只有删除会作废 id; 替换时旧 id 仍然有效, 而新任务不一定回传 id。
    """
    document = _document(status=DocumentStatus.INDEXED, openrag_document_id="orag-old")
    document.mark_indexing()
    assert document.openrag_document_id == "orag-old"

    document.mark_indexed(None)  # 任务没回传 id
    assert document.openrag_document_id == "orag-old"
