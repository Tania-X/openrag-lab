"""Document ingestion helpers for OpenRAG."""

from __future__ import annotations

from pathlib import Path

from openrag_lab.client import OpenRAGClient


def ingest_file(client: OpenRAGClient, path: Path) -> dict:
    """Ingest a single file into OpenRAG."""
    return client.upload_document(path)


def ingest_directory(client: OpenRAGClient, directory: Path) -> list[dict]:
    """Recursively ingest supported documents under a directory.

    Supported extensions are intentionally conservative for the first version.
    Docling itself can handle more; we whitelist here to avoid accidental
    uploads of hidden files or editor artifacts.
    """
    supported = {
        ".md",
        ".txt",
        ".pdf",
        ".docx",
        ".xlsx",
        ".csv",
        ".html",
        ".htm",
    }

    results: list[dict] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in supported:
            continue
        if path.name.startswith("."):
            continue
        print(f"ingesting: {path}")
        results.append(ingest_file(client, path))
    return results
