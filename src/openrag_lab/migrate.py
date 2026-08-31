"""Helpers for migrating reusable assets from dify-rag-lab into OpenRAG Lab.

We intentionally do NOT migrate Dify's internal database or Weaviate data.
The reusable assets are:
- original sample documents
- evaluation CSV files
- metadata field conventions (year / doc_type / version)
- model provider choices (DeepSeek, SiliconFlow BGE)
"""

from __future__ import annotations

import shutil
from pathlib import Path


def sync_sample_data(source: Path, target: Path) -> None:
    """Copy sample documents from dify-rag-lab into this repo's data directory."""
    if not source.exists():
        raise FileNotFoundError(f"Dify sample data not found: {source}")

    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    print(f"synced {source} -> {target}")


def sync_eval_sets(source: Path, target: Path) -> None:
    """Copy evaluation CSVs from dify-rag-lab into configs/eval."""
    if not source.exists():
        raise FileNotFoundError(f"Dify eval directory not found: {source}")

    target.mkdir(parents=True, exist_ok=True)
    for csv_file in source.glob("*.csv"):
        shutil.copy2(csv_file, target / csv_file.name)
        print(f"synced {csv_file.name}")
