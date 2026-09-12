"""Tests for the legacy-document migration helper.

The migration touches real data, so the safety rules matter: a legacy copy may
only be removed once its namespaced replacement really exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openrag_lab.client import OpenRAGError
from openrag_lab.reingest import delete_legacy_copies, reingest_legacy_documents


class FakeClient:
    """Minimal OpenRAGClient stand-in."""

    def __init__(
        self,
        *,
        remote: list[str],
        fail_for: set[str] | None = None,
    ) -> None:
        self.remote = list(remote)
        self.fail_for = fail_for or set()
        self.ingested: list[str] = []
        self.deleted: list[str] = []

    def list_files(self) -> list[dict[str, Any]]:
        return [{"filename": name} for name in self.remote]

    def ingest_file(self, path: Path, wait: bool = True, filename: str | None = None):
        assert filename is not None
        if path.name in self.fail_for:
            raise OpenRAGError(f"boom: {path.name}")
        self.ingested.append(filename)
        self.remote.append(filename)
        return {"status": "completed", "failed_files": 0}

    def delete_document(self, filename: str) -> dict[str, Any]:
        self.deleted.append(filename)
        self.remote.remove(filename)
        return {"success": True, "deleted_chunks": 1}


def _source(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("# doc\n")
    return tmp_path


def test_reingest_namespaces_registers_and_removes_legacy(tmp_path: Path) -> None:
    source = _source(tmp_path, "a.md", "b.md")
    client = FakeClient(remote=["a.md", "b.md"])
    registered: list[tuple[str, str]] = []

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
        register=lambda stored, display, path, task: registered.append((stored, display)),
    )
    delete_legacy_copies(client, report)  # type: ignore[arg-type]

    assert report.reingested == ["default/a.md", "default/b.md"]
    assert registered == [("default/a.md", "a.md"), ("default/b.md", "b.md")]
    assert report.legacy_removable == ["a.md", "b.md"]
    assert client.deleted == ["a.md", "b.md"]
    # After the run the library only holds namespaced documents.
    assert client.remote == ["default/a.md", "default/b.md"]


def test_a_failed_reingest_keeps_the_legacy_copy(tmp_path: Path) -> None:
    """Deleting a legacy copy whose replacement does not exist would lose data."""
    source = _source(tmp_path, "ok.md", "broken.md")
    client = FakeClient(remote=["ok.md", "broken.md"], fail_for={"broken.md"})

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
    )
    delete_legacy_copies(client, report)  # type: ignore[arg-type]

    assert report.reingested == ["default/ok.md"]
    assert [name for name, _ in report.failed] == ["broken.md"]
    assert report.legacy_kept == ["broken.md"]
    assert client.deleted == ["ok.md"]
    assert "broken.md" in client.remote


def test_already_namespaced_documents_are_registered_again(tmp_path: Path) -> None:
    """Rerunning the migration is idempotent and still registers rows."""
    source = _source(tmp_path, "a.md")
    client = FakeClient(remote=["default/a.md"])
    registered: list[str] = []

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
        register=lambda stored, display, path, task: registered.append(stored),
    )

    assert report.already_present == ["default/a.md"]
    assert report.reingested == []
    assert registered == ["default/a.md"]
    assert client.ingested == []


def test_remote_documents_without_a_local_file_are_reported_not_deleted(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, "a.md")
    client = FakeClient(remote=["a.md", "no-copy.md"])

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
    )
    delete_legacy_copies(client, report)  # type: ignore[arg-type]

    assert report.no_local_source == ["no-copy.md"]
    assert "no-copy.md" in client.remote
    assert client.deleted == ["a.md"]


def test_legacy_copies_already_replaced_in_place_are_not_a_failure(
    tmp_path: Path,
) -> None:
    """OpenRAG can replace chunks during re-ingest, leaving nothing to delete."""

    class GoneClient(FakeClient):
        def delete_document(self, filename: str) -> dict[str, Any]:
            from openrag_lab.client import OpenRAGError

            raise OpenRAGError(
                "OpenRAG DELETE /api/v1/documents -> 404",
                status_code=404,
                payload={
                    "success": False,
                    "deleted_chunks": 0,
                    "error": "No matching document chunks were deleted.",
                },
            )

    source = _source(tmp_path, "a.md")
    client = GoneClient(remote=["a.md"])

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
    )
    delete_legacy_copies(client, report)  # type: ignore[arg-type]

    assert report.already_gone == ["a.md"]
    assert report.delete_failed == []


def test_a_name_the_api_would_reject_is_reported_not_ingested(tmp_path: Path) -> None:
    """The migration applies the same storage-name rule as the upload path.

    A backslash is legal on APFS/ext4 but rejected by the rule (and a real
    length overflow is unreachable here, since the filesystem caps a name well
    below MAX_STORED_FILENAME_LENGTH).
    """
    source = _source(tmp_path, "ok.md")
    bad_name = "report\\draft.md"
    (tmp_path / bad_name).write_text("# doc\n")
    client = FakeClient(remote=[])

    report = reingest_legacy_documents(
        client,  # type: ignore[arg-type]
        tenant_slug="default",
        source=source,
    )

    assert report.reingested == ["default/ok.md"]
    assert [name for name, _ in report.failed] == [bad_name]
    assert "Invalid document filename" in report.failed[0][1]
    assert client.ingested == ["default/ok.md"]


def test_nothing_to_delete_uses_the_structured_error() -> None:
    """No string matching: the client carries status and payload."""
    from openrag_lab.client import OpenRAGError
    from openrag_lab.reingest import _nothing_to_delete

    already_gone = OpenRAGError(
        "OpenRAG DELETE ... -> 404",
        status_code=404,
        payload={"success": False, "deleted_chunks": 0, "error": "No matching document"},
    )
    removed = OpenRAGError(
        "OpenRAG DELETE ... -> 200",
        status_code=200,
        payload={"success": True, "deleted_chunks": 3},
    )
    opaque = OpenRAGError("OpenRAG DELETE ... -> 500: boom")

    assert _nothing_to_delete(already_gone) is True
    assert _nothing_to_delete(removed) is False
    assert _nothing_to_delete(opaque) is False
