"""Lifecycle helpers for upload staging files.

Uploads are streamed to a temporary file before they are handed to OpenRAG, and
that file is removed in a ``finally`` block. A process killed during the (bounded
but multi-second) ingestion wait cannot run it, so a startup sweep removes what
a previous run left behind.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

STAGING_PREFIX = "openrag-upload-"

#: Only files older than this are swept, so a concurrent upload cannot lose its
#: staging file to another worker starting up.
SWEEP_AGE_MARGIN_SECONDS = 60.0


def sweep_stale_uploads(
    ingest_timeout_seconds: float, *, now: float | None = None
) -> int:
    """Delete leftover staging files and return how many were removed."""
    cutoff = (now if now is not None else time.time()) - (
        ingest_timeout_seconds + SWEEP_AGE_MARGIN_SECONDS
    )
    removed = 0
    for path in Path(tempfile.gettempdir()).glob(f"{STAGING_PREFIX}*"):
        try:
            if not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            # Another process may have removed it first; not worth failing startup.
            continue
    return removed
