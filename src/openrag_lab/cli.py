"""Command-line interface for OpenRAG Lab."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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
    openrag_rerank: bool = typer.Option(False, "--openrag-rerank", help="Enable OpenRAG rerank."),
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
                rerank=openrag_rerank,
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


@app.command()
def reingest_legacy(
    tenant: str = typer.Option("default", "--tenant", help="Tenant slug to store documents under."),  # noqa: B008
    source: Path = typer.Option(  # noqa: B008
        Path("data/sample-data"),
        "--source",
        "-s",
        help="Directory holding the original files.",
    ),
    delete_legacy: bool = typer.Option(
        False,
        "--delete-legacy",
        help="Delete the unprefixed copies from OpenRAG once they are re-ingested.",
    ),
) -> None:
    """Re-ingest pre-namespace documents as `<tenant slug>/<filename>`.

    Documents ingested before s1p3a have no tenant prefix, so no tenant's
    search scope can reach them; this moves them into the namespace and
    registers them.
    """
    asyncio.run(_reingest_legacy(tenant, source, delete_legacy))


def _guess_mimetype(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def _reingest_legacy(tenant_slug: str, source: Path, delete_legacy: bool) -> None:
    from openrag_lab.client import OpenRAGClient
    from openrag_lab.domain.identity.models import Document
    from openrag_lab.domain.shared.ids import DocumentId
    from openrag_lab.infrastructure.db.repositories.identity import (
        SqlDocumentRepository,
        SqlTenantRepository,
        SqlUserRepository,
    )
    from openrag_lab.infrastructure.db.session import create_all, get_session
    from openrag_lab.reingest import delete_legacy_copies, reingest_legacy_documents

    settings = get_settings()
    await create_all()

    async with asynccontextmanager(get_session)() as session:
        tenant = await SqlTenantRepository(session).find_by_slug(tenant_slug)
        if tenant is None:
            console.print(f"[red]Tenant not found: {tenant_slug}[/red]")
            raise typer.Exit(code=1)

        documents = SqlDocumentRepository(session)
        users = SqlUserRepository(session)
        uploader = await users.find_by_username(settings.bootstrap_admin_username)
        if uploader is None:
            # documents.uploaded_by references users.id: fall back to a real
            # user of the tenant rather than writing a dangling id.
            tenant_users = await users.list_by_tenant(tenant.id)
            uploader = tenant_users[0] if tenant_users else None
        if uploader is None:
            console.print(
                "[red]No user found to attribute migrated documents to. "
                "Bootstrap the tenant (or create a user) first.[/red]"
            )
            raise typer.Exit(code=1)
        uploaded_by = uploader.id
        pending: list[Document] = []

        def register(
            stored_filename: str, display_name: str, path: Path, task: dict
        ) -> None:
            """Collect one registry row; written after the client is closed."""
            pending.append(
                Document(
                    id=DocumentId.generate(),
                    tenant_id=tenant.id,
                    stored_filename=stored_filename,
                    display_name=display_name,
                    uploaded_by=uploaded_by,
                    mimetype=_guess_mimetype(path),
                    size_bytes=path.stat().st_size,
                    openrag_document_id=str(task.get("document_id") or "") or None,
                )
            )

        with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
            report = reingest_legacy_documents(
                client,
                tenant_slug=tenant_slug,
                source=source,
                register=register,
            )

        # Persist the registry before anything destructive runs: the registry is
        # what makes the documents reachable, so it must not depend on the
        # cleanup step succeeding.
        for document in pending:
            existing = await documents.find_by_stored_filename(
                tenant.id, document.stored_filename
            )
            if existing is None:
                await documents.save(document)
            else:
                existing.size_bytes = document.size_bytes
                existing.mimetype = document.mimetype
                await documents.save(existing)
        await session.commit()

        if delete_legacy:
            with OpenRAGClient(
                settings.openrag_base_url, settings.openrag_api_key
            ) as client:
                delete_legacy_copies(client, report)

    table = Table(title=f"Legacy re-ingest into {report.namespace}")
    table.add_column("Result")
    table.add_column("Count")
    table.add_row("re-ingested", str(len(report.reingested)))
    table.add_row("already namespaced", str(len(report.already_present)))
    table.add_row("failed", str(len(report.failed)))
    table.add_row("legacy copies removed", str(len(report.deleted_legacy)))
    table.add_row("legacy removable", str(len(report.legacy_removable)))
    table.add_row("legacy kept (migration failed)", str(len(report.legacy_kept)))
    table.add_row("legacy already gone", str(len(report.already_gone)))
    table.add_row("legacy delete failed", str(len(report.delete_failed)))
    table.add_row("no local source", str(len(report.no_local_source)))
    console.print(table)

    for name, reason in report.failed:
        console.print(f"[red]failed:[/red] {name}: {reason}")
    if report.legacy_kept:
        console.print(
            "[yellow]Kept the legacy copy for:[/yellow] " + ", ".join(report.legacy_kept)
        )
    for name, reason in report.delete_failed:
        console.print(f"[red]delete failed:[/red] {name}: {reason}")
    if report.no_local_source:
        console.print(
            "[yellow]Left in place (no local file to re-ingest):[/yellow] "
            + ", ".join(report.no_local_source)
        )


if __name__ == "__main__":
    app()
