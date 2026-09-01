"""Metadata / knowledge-filter mapping between Dify and OpenRAG.

OpenRAG's public search API does not support arbitrary per-chunk metadata
filters like Dify's ``metadata_filtering_conditions``. It supports reusable
knowledge filters built from concrete dimensions:

- ``data_sources``: exact filenames
- ``document_types``: MIME types
- ``owners`` / ``connector_types``

For the Dify-era year/version metadata, the practical OpenRAG equivalent is
to select the matching source files by filename (e.g. ``40-2024-...``).
This module keeps that mapping explicit and easy to extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EXTENSION_TO_MIMETYPE = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
}


@dataclass
class MetadataCondition:
    """A single Dify-style metadata filter condition."""

    name: str
    operator: str = "is"
    value: str | int | float | None = None


@dataclass
class KnowledgeFilter:
    """A Dify-style knowledge filter (kept for backward compatibility)."""

    logical_operator: str = "and"
    conditions: list[MetadataCondition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "logical_operator": self.logical_operator,
            "conditions": [
                {
                    "name": c.name,
                    "comparison_operator": c.operator,
                    "value": c.value,
                }
                for c in self.conditions
            ],
        }


def dify_metadata_to_openrag_filter(metadata: dict[str, str]) -> KnowledgeFilter:
    """Convert Dify-style metadata to the old Dify-shaped filter object.

    This is kept as a compatibility shim; for actual OpenRAG calls use
    :func:`dify_metadata_to_openrag_filters`.
    """
    return KnowledgeFilter(
        conditions=[MetadataCondition(name=key, operator="is", value=value) for key, value in metadata.items()]
    )


def _mimetypes_for_doc_type(doc_type: str) -> list[str]:
    """Map a Dify-style doc_type to likely OpenRAG document_types.

    doc_type can be a friendly label like "markdown" or a filename extension
    like ".pdf". We normalize both to MIME types.
    """
    normalized = doc_type.lower().strip().lstrip(".")
    by_ext = EXTENSION_TO_MIMETYPE.get(f".{normalized}")
    if by_ext:
        return [by_ext]
    # Common labels
    mapping = {
        "markdown": "text/markdown",
        "md": "text/markdown",
        "text": "text/plain",
        "txt": "text/plain",
        "pdf": "application/pdf",
        "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "html": "text/html",
        "htm": "text/html",
    }
    return [mapping[normalized]] if normalized in mapping else []


def dify_metadata_to_openrag_filters(
    metadata: dict[str, str],
    files: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Convert Dify-style metadata to OpenRAG search filters.

    Supported mappings:
    - ``year`` / ``version`` -> ``data_sources`` by matching the value in filenames
    - ``doc_type`` -> ``document_types`` by MIME type

    If ``files`` is provided, unknown/empty mappings are resolved against the
    actual ingested file list. If no concrete filter can be built, an empty
    dict is returned (meaning unfiltered search).
    """
    filters: dict[str, list[str]] = {}
    data_sources: list[str] = []
    document_types: list[str] = []

    for key, value in metadata.items():
        if value is None or value == "":
            continue
        normalized_key = key.strip().lower()
        normalized_value = str(value).strip()

        if normalized_key in {"year", "version"}:
            if files:
                matches = [
                    f["filename"]
                    for f in files
                    if normalized_value in f.get("filename", "")
                ]
                data_sources.extend(matches)
            # Without a file list we cannot resolve filename-based filters.
        elif normalized_key in {"doc_type", "document_type", "type"}:
            document_types.extend(_mimetypes_for_doc_type(normalized_value))

    if data_sources:
        filters["data_sources"] = sorted(set(data_sources))
    if document_types:
        filters["document_types"] = sorted(set(document_types))

    return filters
