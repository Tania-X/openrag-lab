"""Which document formats openrag-lab will hand to OpenRAG.

Kept in the domain so the API upload path and the CLI ingestion helpers share
one list: a file the CLI would skip must not be accepted by the HTTP API.
"""

from __future__ import annotations

from pathlib import Path

#: Formats openrag-lab accepts. Anything else is rejected before it reaches
#: OpenRAG's parsers.
SUPPORTED_DOCUMENT_EXTENSIONS = frozenset(
    {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv", ".html", ".htm"}
)


def is_supported_document(filename: str) -> bool:
    """True when ``filename`` has a format openrag-lab ingests."""
    return Path(filename).suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS
