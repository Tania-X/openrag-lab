"""One-command Dify vs OpenRAG comparison report generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openrag_lab.client import OpenRAGClient
from openrag_lab.comparison import (
    evaluate_dify_row,
    evaluate_openrag_row,
    summarize_platform,
)
from openrag_lab.config import get_settings
from openrag_lab.dify import DifyClient
from openrag_lab.eval import EvalResult, load_eval_csv


@dataclass
class CompareSpec:
    """One row in the comparison report."""

    name: str
    csv: str
    query_field: str = "auto"
    dify_rerank: bool = False
    openrag_rerank: bool = False
    use_metadata: bool = False
    note: str = ""


DEFAULT_SPECS: list[CompareSpec] = [
    CompareSpec(
        name="金融 15 题 baseline",
        csv="configs/eval/fintech-评测集-questions.csv",
        note="hybrid_search，无 rerank，无 metadata",
    ),
    CompareSpec(
        name="金融 15 题 Dify rerank",
        csv="configs/eval/fintech-评测集-questions.csv",
        dify_rerank=True,
        note="Dify 开启 BAAI/bge-reranker-v2-m3",
    ),
    CompareSpec(
        name="金融 15 题 OpenRAG rerank",
        csv="configs/eval/fintech-评测集-questions.csv",
        openrag_rerank=True,
        note="OpenRAG search + BAAI/bge-reranker-v2-m3",
    ),
    CompareSpec(
        name="金融 15 题 双端 rerank",
        csv="configs/eval/fintech-评测集-questions.csv",
        dify_rerank=True,
        openrag_rerank=True,
        note="Dify rerank vs OpenRAG rerank",
    ),
    CompareSpec(
        name="Batch1 baseline",
        csv="configs/eval/fintech-batch1-评测集-questions-standard.csv",
        note="hybrid_search，无 rerank，无 metadata",
    ),
    CompareSpec(
        name="Batch1 Dify rerank",
        csv="configs/eval/fintech-batch1-评测集-questions-standard.csv",
        dify_rerank=True,
    ),
    CompareSpec(
        name="Batch1 OpenRAG rerank",
        csv="configs/eval/fintech-batch1-评测集-questions-standard.csv",
        openrag_rerank=True,
    ),
    CompareSpec(
        name="Batch1 双端 rerank",
        csv="configs/eval/fintech-batch1-评测集-questions-standard.csv",
        dify_rerank=True,
        openrag_rerank=True,
    ),
    CompareSpec(
        name="Batch2 baseline",
        csv="configs/eval/fintech-batch2-评测集-questions-standard.csv",
        note="hybrid_search，无 rerank，无 metadata",
    ),
    CompareSpec(
        name="Batch2 Dify rerank",
        csv="configs/eval/fintech-batch2-评测集-questions-standard.csv",
        dify_rerank=True,
    ),
    CompareSpec(
        name="Batch2 OpenRAG rerank",
        csv="configs/eval/fintech-batch2-评测集-questions-standard.csv",
        openrag_rerank=True,
    ),
    CompareSpec(
        name="Batch2 双端 rerank",
        csv="configs/eval/fintech-batch2-评测集-questions-standard.csv",
        dify_rerank=True,
        openrag_rerank=True,
    ),
    CompareSpec(
        name="Batch3 baseline",
        csv="configs/eval/fintech-batch3-评测集-questions-standard.csv",
        note="hybrid_search，无 rerank，无 metadata",
    ),
    CompareSpec(
        name="Batch3 Dify rerank",
        csv="configs/eval/fintech-batch3-评测集-questions-standard.csv",
        dify_rerank=True,
    ),
    CompareSpec(
        name="Batch3 OpenRAG rerank",
        csv="configs/eval/fintech-batch3-评测集-questions-standard.csv",
        openrag_rerank=True,
    ),
    CompareSpec(
        name="Batch3 双端 rerank",
        csv="configs/eval/fintech-batch3-评测集-questions-standard.csv",
        dify_rerank=True,
        openrag_rerank=True,
    ),
    CompareSpec(
        name="异构格式 baseline",
        csv="configs/eval/fintech-heterogeneous-评测集-questions.csv",
        note="PDF/DOCX/XLSX/HTML/CSV",
    ),
    CompareSpec(
        name="异构格式 Dify rerank",
        csv="configs/eval/fintech-heterogeneous-评测集-questions.csv",
        dify_rerank=True,
    ),
    CompareSpec(
        name="异构格式 OpenRAG rerank",
        csv="configs/eval/fintech-heterogeneous-评测集-questions.csv",
        openrag_rerank=True,
    ),
    CompareSpec(
        name="异构格式 双端 rerank",
        csv="configs/eval/fintech-heterogeneous-评测集-questions.csv",
        dify_rerank=True,
        openrag_rerank=True,
    ),
    CompareSpec(
        name="年份题 无过滤",
        csv="configs/eval/fintech-metadata-year-评测集-rewrite-ab.csv",
        note="original_query",
    ),
    CompareSpec(
        name="年份题 元数据过滤",
        csv="configs/eval/fintech-metadata-year-评测集-rewrite-ab.csv",
        use_metadata=True,
        note="Dify metadata_filtering_conditions / OpenRAG data_sources",
    ),
    CompareSpec(
        name="Query Rewrite original",
        csv="configs/eval/fintech-评测集-rewrite-ab.csv",
        query_field="original_query",
        note="使用评测集预生成的 original_query",
    ),
    CompareSpec(
        name="Query Rewrite rewritten",
        csv="configs/eval/fintech-评测集-rewrite-ab.csv",
        query_field="rewritten_query",
        note="使用评测集预生成的 rewritten_query",
    ),
]


def _run_spec(
    spec: CompareSpec,
    dify_client: DifyClient,
    openrag_client: OpenRAGClient,
    top_k: int,
) -> tuple[str, str, str, str, str, str]:
    """Run one spec and return Dify/OpenRAG metric strings."""
    rows = load_eval_csv(Path(spec.csv), query_field=spec.query_field)
    if not rows:
        raise ValueError(f"No rows loaded from {spec.csv}")

    files = openrag_client.list_files() if spec.use_metadata else []

    dify_results: list[EvalResult] = []
    openrag_results: list[EvalResult] = []

    for row in rows:
        dify_results.append(
            evaluate_dify_row(
                dify_client,
                row,
                top_k=top_k,
                rerank=spec.dify_rerank,
                use_metadata=spec.use_metadata,
            )
        )
        openrag_results.append(
            evaluate_openrag_row(
                openrag_client,
                row,
                top_k=top_k,
                use_metadata=spec.use_metadata,
                files=files,
                rerank=spec.openrag_rerank,
            )
        )

    dify = summarize_platform("Dify", dify_results)
    openrag = summarize_platform("OpenRAG", openrag_results)

    def fmt(p) -> str:
        return (
            f"{p.hit1}/{p.total} ({p.hit1_rate:.1%}) / "
            f"{p.hitk}/{p.total} ({p.hitk_rate:.1%}) / MRR {p.mrr:.4f}"
        )

    return (
        spec.name,
        spec.note or spec.csv,
        f"{len(rows)}",
        fmt(dify),
        fmt(openrag),
        "✅" if dify.hit1_rate > openrag.hit1_rate else (
            "❌" if dify.hit1_rate < openrag.hit1_rate else "="
        ),
    )


def generate_report(
    specs: list[CompareSpec] | None = None,
    *,
    top_k: int = 5,
    output: Path = Path("docs/comparison-report.md"),
) -> Path:
    """Run all comparison specs and write a Markdown report."""
    settings = get_settings()
    specs = specs or DEFAULT_SPECS

    lines: list[str] = []
    lines.append("# Dify vs OpenRAG 对比报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(UTC).isoformat(timespec='seconds')}")
    lines.append(f"- top_k：{top_k}")
    lines.append(f"- Dify 知识库：{settings.dify_dataset_id}")
    lines.append(f"- OpenRAG：{settings.openrag_base_url}")
    lines.append("")
    lines.append("> 注意：当前只对比检索层。Dify 知识库为 50 份金融文档，OpenRAG 为 58 份；")
    lines.append("> 报告只使用两边都覆盖的金融评测集。")
    lines.append("")
    lines.append("| 实验 | 说明 | 条数 | Dify hit@1 / hit@k / MRR | OpenRAG hit@1 / hit@k / MRR | Dify hit@1 胜出 |")
    lines.append("|---|---|---:|---|---|---|")

    with DifyClient(
        settings.dify_base_url,
        settings.dify_dataset_id,
        settings.dify_dataset_api_key,
    ) as dify_client, OpenRAGClient(
        settings.openrag_base_url,
        settings.openrag_api_key,
    ) as openrag_client:
        for spec in specs:
            try:
                name, note, count, dify_str, openrag_str, winner = _run_spec(
                    spec,
                    dify_client,
                    openrag_client,
                    top_k=top_k,
                )
                lines.append(
                    f"| {name} | {note} | {count} | {dify_str} | {openrag_str} | {winner} |"
                )
            except Exception as exc:  # noqa: BLE001 - report should not die on one spec
                lines.append(
                    f"| {spec.name} | {spec.note or spec.csv} | - | 错误：{exc} | - | - |"
                )

    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- `hit@1 / hit@5 / MRR` 均来自检索结果，不涉及 LLM 生成。")
    lines.append("- `Dify rerank` 表示 Dify 开启 `BAAI/bge-reranker-v2-m3`；`OpenRAG rerank` 表示 OpenRAG `/api/v1/search` 开启同一 reranker。")
    lines.append("- `元数据过滤` 在 Dify 使用 `metadata_filtering_conditions`，在 OpenRAG 使用 `data_sources` 文件名过滤。")
    lines.append("- Query Rewrite 使用评测集里预生成的 `rewritten_query`，不是在线调用改写服务。")
    lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
