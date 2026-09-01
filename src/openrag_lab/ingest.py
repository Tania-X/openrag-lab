"""Document ingestion helpers for OpenRAG."""

from __future__ import annotations

from pathlib import Path

from openrag_lab.client import OpenRAGClient

SUPPORTED_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".html",
    ".htm",
}


def _is_eval_csv(path: Path) -> bool:
    """Skip Dify-era evaluation CSVs that live beside real documents."""
    name = path.name
    return any(
        marker in name
        for marker in ("评测集", "questions", "rewrite-ab")
    )


def iter_supported_files(directory: Path) -> list[Path]:
    """Recursively collect supported documents under a directory."""
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        if _is_eval_csv(path):
            continue
        files.append(path)
    return files


def ingest_file(client: OpenRAGClient, path: Path, wait: bool = True) -> dict:
    """Ingest a single file into OpenRAG."""
    return client.ingest_file(path, wait=wait)


def ingest_directory(
    client: OpenRAGClient,
    directory: Path,
    *,
    wait: bool = True,
    max_files: int | None = None,
) -> list[dict]:
    """Ingest supported documents under a directory.

    Returns one task/status result per file. By default it waits for each
    file's ingestion task so failures are easy to attribute.
    """
    files = iter_supported_files(directory)
    if max_files is not None:
        files = files[:max_files]

    results: list[dict] = []
    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] ingesting: {path}")
        result = ingest_file(client, path, wait=wait)
        if wait:
            state = result.get("status")
            if state == "completed":
                print(f"  -> completed ({result.get('successful_files', 0)} ok)")
            else:
                print(f"  -> {state}")
        else:
            print(f"  -> task_id={result.get('task_id')}")
        results.append(result)
    return results
