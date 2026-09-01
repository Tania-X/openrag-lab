"""Comparison helpers for Dify vs OpenRAG retrieval evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openrag_lab.client import OpenRAGClient
from openrag_lab.dify import DifyClient
from openrag_lab.eval import EvalResult, EvalRow
from openrag_lab.metadata import dify_metadata_to_openrag_filters


@dataclass
class PlatformResult:
    """Retrieval metrics for one platform on one eval set."""

    platform: str
    total: int
    hit1: int
    hitk: int
    mrr_sum: float
    results: list[EvalResult]

    @property
    def hit1_rate(self) -> float:
        return self.hit1 / self.total if self.total else 0.0

    @property
    def hitk_rate(self) -> float:
        return self.hitk / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        return self.mrr_sum / self.total if self.total else 0.0


def _rank_from_dify_records(records: list[dict[str, Any]], keyword: str) -> int | None:
    for index, record in enumerate(records, start=1):
        content = (record.get("segment") or {}).get("content") or ""
        if keyword in content:
            return index
    return None


def evaluate_dify_row(
    client: DifyClient,
    row: EvalRow,
    *,
    top_k: int = 5,
    rerank: bool = False,
    use_metadata: bool = False,
) -> EvalResult:
    """Evaluate one row against Dify retrieval."""
    metadata = row.metadata if use_metadata else None
    data = client.retrieve(
        row.question,
        top_k=top_k,
        rerank=rerank,
        metadata=metadata,
    )
    rank = _rank_from_dify_records(data.get("records", []), row.expected_keyword)
    return EvalResult(
        row=row,
        rank=rank,
        hit1=rank == 1,
        hitk=rank is not None and rank <= top_k,
        mrr=(1.0 / rank) if rank else 0.0,
    )


def evaluate_openrag_row(
    client: OpenRAGClient,
    row: EvalRow,
    *,
    top_k: int = 5,
    use_metadata: bool = False,
    files: list[dict[str, Any]] | None = None,
) -> EvalResult:
    """Evaluate one row against OpenRAG search."""
    filters = (
        dify_metadata_to_openrag_filters(row.metadata, files)
        if use_metadata
        else None
    )
    data = client.search(row.question, limit=top_k, filters=filters)
    texts: list[str] = []
    for item in data.get("results", []):
        text = item.get("text") or ""
        if text:
            texts.append(text)

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


def summarize_platform(platform: str, results: list[EvalResult]) -> PlatformResult:
    total = len(results)
    return PlatformResult(
        platform=platform,
        total=total,
        hit1=sum(1 for r in results if r.hit1),
        hitk=sum(1 for r in results if r.hitk),
        mrr_sum=sum(r.mrr for r in results),
        results=results,
    )
