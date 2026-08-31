"""Metadata / knowledge-filter mapping between Dify and OpenRAG.

Dify side used custom metadata fields: year, doc_type, version.
OpenRAG side uses knowledge filters; the exact API shape still needs to be
confirmed against the installed OpenRAG version, so this module is a seam for
that mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetadataCondition:
    """A single metadata filter condition."""

    name: str
    operator: str = "is"
    value: str | int | float | None = None


@dataclass
class KnowledgeFilter:
    """A knowledge filter that can be attached to an ingest or search request."""

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
    """Convert Dify-style metadata (e.g. {"year": "2025"}) to a KnowledgeFilter."""
    return KnowledgeFilter(
        conditions=[MetadataCondition(name=key, operator="is", value=value) for key, value in metadata.items()]
    )
