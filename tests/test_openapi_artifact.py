"""Guards for the committed contract artifacts.

The self-API contract used to be written by hand and silently fell behind the
code (it listed 4 of 13 routes while auth/users/tenants/roles already existed).
It is generated now, and these tests fail when it drifts.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from openrag_lab.api.main import app
from openrag_lab.config import get_settings
from openrag_lab.openapi_export import (
    PUBLIC_OPERATIONS,
    SCHEME_NAME,
    build_spec,
    documented_operations,
    render_yaml,
)

ARTIFACT = Path("openapi/openrag-lab.yaml")


def test_the_generated_contract_matches_the_committed_file() -> None:
    """Run `openrag-lab export-openapi` if this fails."""
    committed = ARTIFACT.read_text(encoding="utf-8")
    regenerated = render_yaml(build_spec(app))
    assert committed == regenerated, (
        "openapi/openrag-lab.yaml is out of date with the app; "
        "regenerate it with `openrag-lab export-openapi`"
    )


def test_the_contract_lists_every_route_the_app_serves() -> None:
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    served = set(app.openapi()["paths"])
    assert set(spec["paths"]) == served
    # A sanity floor so a broken import cannot make both sides empty.
    assert len(served) >= 13


def test_only_the_declared_public_operations_skip_auth() -> None:
    """Auth marking is explicit: a new public endpoint must be declared."""
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    unauthenticated = {
        path
        for path, operations in spec["paths"].items()
        if any("security" not in op for op in operations.values() if isinstance(op, dict))
    }
    assert unauthenticated == set(PUBLIC_OPERATIONS)


def test_authenticated_operations_use_the_bearer_scheme() -> None:
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    assert SCHEME_NAME in spec["components"]["securitySchemes"]
    me = spec["paths"]["/api/auth/me"]["get"]
    assert me["security"] == [{SCHEME_NAME: []}]


def _openrag_openapi_url() -> str | None:
    """Return the live OpenRAG spec URL when the deployment is reachable."""
    base = get_settings().openrag_base_url.rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    host, port = parsed.hostname, parsed.port or 80
    if not host:
        return None
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except OSError:
        return None
    return f"{base}/api/openapi.json"


def test_the_upstream_contract_still_exists_in_the_live_spec() -> None:
    """Verify openrag.yaml against a running OpenRAG, when there is one.

    The public v1 surface is documented upstream as `/v1/...`; our subset uses
    the `/api/v1/...` prefix of the proxy we call through.
    """
    url = _openrag_openapi_url()
    if url is None:
        pytest.skip("OpenRAG is not reachable; skipping the live contract check")

    import httpx

    live = httpx.get(url, timeout=10).json()
    live_paths = live.get("paths", {})

    missing: list[str] = []
    for path, methods in documented_operations().items():
        upstream = "/" + path.removeprefix("/api/")
        for method in methods:
            if method.lower() not in {m.lower() for m in live_paths.get(upstream, {})}:
                missing.append(f"{method} {path} (expected {upstream})")

    assert not missing, (
        "openapi/openrag.yaml documents endpoints OpenRAG no longer serves: "
        + json.dumps(missing, ensure_ascii=False)
    )
