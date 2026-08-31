"""Command-line interface for OpenRAG Lab."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from openrag_lab.config import get_settings
from openrag_lab.eval import evaluate_row, load_eval_csv
from openrag_lab.ingest import ingest_directory
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
) -> None:
    """Ingest a directory of documents into OpenRAG."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    client = OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key)
    results = ingest_directory(client, directory)
    console.print(f"Ingested {len(results)} file(s).")


@app.command()
def eval(
    csv_path: Path = typer.Option(None, "--csv", help="Evaluation CSV path."),  # noqa: B008
    top_k: int = typer.Option(5, "--top-k"),  # noqa: B008
) -> None:
    """Run evaluation against OpenRAG."""
    from openrag_lab.client import OpenRAGClient

    settings = get_settings()
    csv_path = csv_path or settings.eval_csv
    client = OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key)

    rows = load_eval_csv(csv_path)
    if not rows:
        console.print("[yellow]No evaluation rows loaded.[/yellow]")
        raise typer.Exit(code=1)

    hit1 = 0
    hitk = 0
    mrr_total = 0.0
    for row in rows:
        result = evaluate_row(client, row, top_k=top_k)
        hit1 += int(result.hit1)
        hitk += int(result.hitk)
        mrr_total += result.mrr

    total = len(rows)
    console.print(f"total={total}")
    console.print(f"hit@1={hit1}/{total}")
    console.print(f"hit@{top_k}={hitk}/{total}")
    console.print(f"MRR={mrr_total / total:.4f}")


@app.command()
def sync_dify_assets() -> None:
    """Copy reusable sample data and eval sets from dify-rag-lab."""
    settings = get_settings()
    sync_sample_data(settings.dify_sample_data_path, Path("data/sample-data"))
    sync_eval_sets(settings.dify_sample_data_path, settings.eval_csv.parent)


if __name__ == "__main__":
    app()
