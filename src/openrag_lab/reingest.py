"""One-off migration of pre-namespace documents into a tenant's namespace.

Documents ingested before s1p3a carry no tenant prefix, so no tenant's search
scope can reach them (the boundary is the registered filename list). This
re-ingests them as ``<tenant namespace><name>`` from a local source directory
and reports what it did.

Two deliberate properties:

* ingesting and deleting legacy copies are **separate steps**, so the caller
  can persist the registry before anything destructive happens;
* a legacy copy is only removed once its namespaced replacement exists, and a
  copy that is already gone is reported rather than treated as a failure.

The registry write is left to the caller through ``register`` so this module
stays independent of the database layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openrag_lab.client import OpenRAGClient, OpenRAGError
from openrag_lab.ingest import iter_supported_files

RegisterDocument = Callable[[str, str, Path, dict[str, Any]], None]


@dataclass
class MigrationReport:
    tenant_slug: str
    namespace: str
    reingested: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    legacy_removable: list[str] = field(default_factory=list)
    legacy_kept: list[str] = field(default_factory=list)
    no_local_source: list[str] = field(default_factory=list)
    deleted_legacy: list[str] = field(default_factory=list)
    already_gone: list[str] = field(default_factory=list)
    delete_failed: list[tuple[str, str]] = field(default_factory=list)


def reingest_legacy_documents(
    client: OpenRAGClient,
    *,
    tenant_slug: str,
    source: Path,
    register: RegisterDocument | None = None,
) -> MigrationReport:
    """Re-ingest every locally available document under the tenant namespace."""
    namespace = f"{tenant_slug}/"
    report = MigrationReport(tenant_slug=tenant_slug, namespace=namespace)

    files = iter_supported_files(source)
    source_names = {path.name for path in files}
    remote_names = {
        str(f.get("filename")) for f in client.list_files() if f.get("filename")
    }

    for path in files:
        stored = f"{namespace}{path.name}"
        if stored in remote_names:
            report.already_present.append(stored)
            if register is not None:
                register(stored, path.name, path, {"status": "completed"})
            continue
        try:
            task = client.ingest_file(path, wait=True, filename=stored)
        except OpenRAGError as exc:
            report.failed.append((path.name, str(exc)))
            continue
        failed_files = int(task.get("failed_files") or 0)
        if task.get("status") != "completed" or failed_files > 0:
            # The task payload carries no reason; the OpenRAG backend logs do.
            report.failed.append(
                (
                    path.name,
                    f"ingestion failed ({failed_files} file(s)); "
                    "see the OpenRAG backend logs for the parser error",
                )
            )
            continue
        report.reingested.append(stored)
        if register is not None:
            register(stored, path.name, path, task)

    # A remote filename with no "/" predates the namespace rule.
    migrated_names = {
        stored[len(namespace) :]
        for stored in report.reingested + report.already_present
    }
    for name in sorted(remote_names):
        if "/" in name:
            continue
        if name in migrated_names:
            report.legacy_removable.append(name)
        elif name in source_names:
            # Its re-ingest did not succeed, so the legacy copy is all we have.
            report.legacy_kept.append(name)
        else:
            # Cannot be re-ingested without a local copy; needs a manual call.
            report.no_local_source.append(name)

    return report


def delete_legacy_copies(client: OpenRAGClient, report: MigrationReport) -> MigrationReport:
    """Remove the legacy copies the migration replaced.

    Only touches ``legacy_removable`` (namespaced replacements confirmed to
    exist) and never raises: a copy that is already gone counts as done, and
    any other failure is reported so the caller can retry or clean up by hand.
    """
    for legacy in report.legacy_removable:
        try:
            client.delete_document(legacy)
        except OpenRAGError as exc:
            if _nothing_to_delete(exc):
                # e.g. OpenRAG replaced the chunks in place during re-ingest.
                report.already_gone.append(legacy)
            else:
                report.delete_failed.append((legacy, str(exc)))
            continue
        report.deleted_legacy.append(legacy)
    return report


def _nothing_to_delete(exc: OpenRAGError) -> bool:
    """True when OpenRAG reports that no chunks matched the filename."""
    text = str(exc)
    return '"deleted_chunks":0' in text.replace(" ", "") or "No matching document" in text
