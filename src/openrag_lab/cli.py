"""Command-line interface for OpenRAG Lab."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openrag_lab.config import get_settings
from openrag_lab.eval import evaluate_row, load_eval_csv, summarize_results
from openrag_lab.ingest import ingest_directory
from openrag_lab.metadata import dify_metadata_to_openrag_filters
from openrag_lab.migrate import sync_eval_sets, sync_sample_data

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
def sync_dify_assets() -> None:
    """Copy reusable sample data and eval sets from dify-rag-lab."""
    settings = get_settings()
    sync_sample_data(settings.dify_sample_data_path, Path("data/sample-data"))
    sync_eval_sets(settings.dify_sample_data_path, settings.eval_csv.parent)


if __name__ == "__main__":
    app()
