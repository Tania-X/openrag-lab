"""Command-line interface for OpenRAG Lab."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openrag_lab.comparison import (
    evaluate_dify_row,
    evaluate_openrag_row,
    summarize_platform,
)
from openrag_lab.config import get_settings
from openrag_lab.dify import DifyClient
from openrag_lab.eval import evaluate_row, load_eval_csv, summarize_results
from openrag_lab.ingest import ingest_directory
from openrag_lab.metadata import dify_metadata_to_openrag_filters
from openrag_lab.migrate import sync_eval_sets, sync_sample_data
from openrag_lab.report import generate_report

app = typer.Typer(help="OpenRAG Lab CLI")
console = Console()


@app.command()
def init() -> None:
    """Initialize local directories and show current configuration."""
    settings = get_settings()
    for path in [
        settings.eval_csv.parent,
        Path("data"),
        Path("configs"),
        Path("docs"),
    ]:
        path.mkdir(parents=True, exist_ok=True)

    console.print("[green]OpenRAG Lab initialized.[/green]")
    console.print(f"  OpenRAG base url : {settings.openrag_base_url}")
    console.print(f"  Eval CSV         : {settings.eval_csv}")


@app.command()
def ingest(
    directory: Path = typer.Option(  # noqa: B008
        Path("data/sample-data"),
        "--directory",
        "-d",
        help="Directory containing documents to ingest.",
    ),
    no_wait: bool = typer.Option(False, "--no-wait", help="Do not wait for ingestion completion."),
    max_files: int | None = typer.Option(None, "--max-files", help="Only ingest the first N files."),
) -> None:
    """Ingest a directory of documents into OpenRAG."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
        results = ingest_directory(
            client,
            directory,
            wait=not no_wait,
            max_files=max_files,
        )
    console.print(f"Ingested {len(results)} file(s).")


@app.command()
def list_files() -> None:
    """List currently ingested files in OpenRAG."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
        files = client.list_files()

    table = Table(title=f"Ingested files ({len(files)})")
    table.add_column("Filename")
    table.add_column("MIME")
    table.add_column("Chunks")
    table.add_column("Model")
    for f in files:
        table.add_row(
            f.get("filename", ""),
            f.get("mimetype", ""),
            str(f.get("chunk_count", "")),
            f.get("embedding_model", ""),
        )
    console.print(table)


@app.command()
def eval(
    csv_path: Path = typer.Option(None, "--csv", help="Evaluation CSV path."),  # noqa: B008
    top_k: int = typer.Option(5, "--top-k"),  # noqa: B008
    use_metadata: bool = typer.Option(False, "--use-metadata", help="Apply metadata_* filters from each row."),
) -> None:
    """Run evaluation against OpenRAG search."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    csv_path = csv_path or settings.eval_csv
    with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
        files = client.list_files() if use_metadata else []
        rows = load_eval_csv(csv_path)
        if not rows:
            console.print("[yellow]No evaluation rows loaded.[/yellow]")
            raise typer.Exit(code=1)

        results = []
        for row in rows:
            filters = dify_metadata_to_openrag_filters(row.metadata, files) if use_metadata else None
            result = evaluate_row(client, row, top_k=top_k, filters=filters)
            results.append(result)

    summary = summarize_results(results, top_k=top_k)
    total = summary["total"]
    console.print(f"total={total}")
    console.print(f"hit@1={summary['hit1']:.4f} ({int(summary['hit1'] * total)}/{total})")
    console.print(f"hit@{top_k}={summary[f'hit@{top_k}']:.4f} ({int(summary[f'hit@{top_k}'] * total)}/{total})")
    console.print(f"MRR={summary['mrr']:.4f}")


@app.command()
def compare(
    csv_path: Path = typer.Option(None, "--csv", help="Evaluation CSV path."),  # noqa: B008
    top_k: int = typer.Option(5, "--top-k"),  # noqa: B008
    dify_rerank: bool = typer.Option(False, "--dify-rerank", help="Enable Dify rerank."),
    use_metadata: bool = typer.Option(False, "--use-metadata", help="Apply metadata filters on both platforms."),
    query_field: str = typer.Option("auto", "--query-field", help="auto|question|original_query|rewritten_query"),
) -> None:
    """Compare Dify and OpenRAG retrieval on the same eval CSV."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    csv_path = csv_path or settings.eval_csv
    rows = load_eval_csv(csv_path, query_field=query_field)
    if not rows:
        console.print("[yellow]No evaluation rows loaded.[/yellow]")
        raise typer.Exit(code=1)

    with DifyClient(
        settings.dify_base_url,
        settings.dify_dataset_id,
        settings.dify_dataset_api_key,
    ) as dify_client:
        dify_results = [
            evaluate_dify_row(
                dify_client,
                row,
                top_k=top_k,
                rerank=dify_rerank,
                use_metadata=use_metadata,
            )
            for row in rows
        ]

    with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as openrag_client:
        files = openrag_client.list_files() if use_metadata else []
        openrag_results = [
            evaluate_openrag_row(
                openrag_client,
                row,
                top_k=top_k,
                use_metadata=use_metadata,
                files=files,
            )
            for row in rows
        ]

    dify_summary = summarize_platform("Dify", dify_results)
    openrag_summary = summarize_platform("OpenRAG", openrag_results)

    table = Table(title=f"Dify vs OpenRAG retrieval ({len(rows)} rows, top_k={top_k})")
    table.add_column("Platform")
    table.add_column("hit@1")
    table.add_column(f"hit@{top_k}")
    table.add_column("MRR")
    table.add_row(
        dify_summary.platform,
        f"{dify_summary.hit1}/{dify_summary.total} ({dify_summary.hit1_rate:.1%})",
        f"{dify_summary.hitk}/{dify_summary.total} ({dify_summary.hitk_rate:.1%})",
        f"{dify_summary.mrr:.4f}",
    )
    table.add_row(
        openrag_summary.platform,
        f"{openrag_summary.hit1}/{openrag_summary.total} ({openrag_summary.hit1_rate:.1%})",
        f"{openrag_summary.hitk}/{openrag_summary.total} ({openrag_summary.hitk_rate:.1%})",
        f"{openrag_summary.mrr:.4f}",
    )
    console.print(table)


@app.command()
def compare_all(
    output: Path = typer.Option(  # noqa: B008
        Path("docs/comparison-report.md"),
        "--output",
        "-o",
        help="Markdown report output path.",
    ),
    top_k: int = typer.Option(5, "--top-k"),  # noqa: B008
) -> None:
    """Run the full Dify vs OpenRAG comparison suite and write a Markdown report."""
    path = generate_report(top_k=top_k, output=output)
    console.print(f"[green]Report written to {path}[/green]")


@app.command()
def sync_dify_assets() -> None:
    """Copy reusable sample data and eval sets from dify-rag-lab."""
    settings = get_settings()
    sync_sample_data(settings.dify_sample_data_path, Path("data/sample-data"))
    sync_eval_sets(settings.dify_sample_data_path, settings.eval_csv.parent)


if __name__ == "__main__":
    app()
