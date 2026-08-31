"""Evaluation helpers for OpenRAG.

The goal is to reuse the same evaluation methodology built in dify-rag-lab:
- hit@1
- hit@k
- MRR

The exact OpenRAG search response schema is still TBD, so the parsing logic is
isolated here to make it easy to adapt.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from openrag_lab.client import OpenRAGClient


@dataclass
class EvalRow:
    """One evaluation row."""

    id: str | None
    question: str
    expected_keyword: str
    metadata: dict[str, str]


@dataclass
class EvalResult:
    """Evaluation result for a single query."""

    row: EvalRow
    rank: int | None
    hit1: bool
    hitk: bool
    mrr: float


def load_eval_csv(path: Path) -> list[EvalRow]:
    """Load an evaluation CSV.

    Expected columns: id, question, expected_keyword, and optional metadata_* columns.
    """
    rows: list[EvalRow] = []
    with path.open(encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            metadata = {
                key.removeprefix("metadata_").strip(): value
                for key, value in raw.items()
                if key.startswith("metadata_") and value
            }
            rows.append(
                EvalRow(
                    id=raw.get("id"),
                    question=raw["question"],
                    expected_keyword=raw["expected_keyword"],
                    metadata=metadata,
                )
            )
    return rows


def _extract_hits(response: dict) -> list[str]:
    """Extract chunk text from an OpenRAG search response.

    Placeholder: adapt this once the real OpenRAG search response is known.
    """
    records = response.get("records") or response.get("results") or []
    texts: list[str] = []
    for record in records:
        segment = record.get("segment") or record.get("chunk") or {}
        text = segment.get("content") or segment.get("text") or ""
        if text:
            texts.append(text)
    return texts


def evaluate_row(client: OpenRAGClient, row: EvalRow, top_k: int = 5) -> EvalResult:
    """Evaluate one row against OpenRAG search."""
    response = client.search(row.question)
    texts = _extract_hits(response)

    rank: int | None = None
    for index, text in enumerate(texts, start=1):
        if row.expected_keyword in text:
            rank = index
            break

    return EvalResult(
        row=row,
        rank=rank,
        hit1=rank == 1,
        hitk=rank is not None and rank <= top_k,
        mrr=(1.0 / rank) if rank else 0.0,
    )
