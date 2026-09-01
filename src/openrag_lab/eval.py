"""Evaluation helpers for OpenRAG.

Reuses the Dify-era methodology:
- hit@1
- hit@k
- MRR

OpenRAG's public search response is a flat ``results`` list:
    [{"filename": ..., "text": ..., "score": ..., "page": ..., "mimetype": ...}]
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    Expected columns: id, question, expected_keyword.
    Optional metadata_* columns are preserved for filter-aware evaluation.
    """
    rows: list[EvalRow] = []
    with path.open(encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            metadata = {
                key.removeprefix("metadata_").strip(): value
                for key, value in raw.items()
                if key.startswith("metadata_") and value
            }
            # Some Dify-era CSVs put metadata_name / metadata_value columns.
            if raw.get("metadata_name") and raw.get("metadata_value"):
                metadata.setdefault(raw["metadata_name"].strip(), raw["metadata_value"].strip())

            rows.append(
                EvalRow(
                    id=raw.get("id"),
                    question=raw["question"] or raw.get("original_query") or "",
                    expected_keyword=raw["expected_keyword"],
                    metadata=metadata,
                )
            )
    return [row for row in rows if row.question and row.expected_keyword]


def _extract_hits(response: dict[str, Any]) -> list[str]:
    """Extract chunk text from an OpenRAG v1 search response."""
    records = response.get("results") or []
    texts: list[str] = []
    for record in records:
        text = record.get("text") or ""
        if text:
            texts.append(text)
    return texts


def evaluate_row(
    client: OpenRAGClient,
    row: EvalRow,
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> EvalResult:
    """Evaluate one row against OpenRAG search."""
    response = client.search(row.question, limit=top_k, filters=filters)
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


def summarize_results(results: list[EvalResult], top_k: int = 5) -> dict[str, float]:
    """Summarize a list of evaluation results."""
    total = len(results)
    if total == 0:
        return {"total": 0, "hit1": 0.0, f"hit@{top_k}": 0.0, "mrr": 0.0}

    hit1 = sum(1 for r in results if r.hit1)
    hitk = sum(1 for r in results if r.hitk)
    mrr = sum(r.mrr for r in results) / total
    return {
        "total": total,
        "hit1": hit1 / total,
        f"hit@{top_k}": hitk / total,
        "mrr": mrr,
    }
