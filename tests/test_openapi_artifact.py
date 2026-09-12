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
    public_operations_in,
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
    """Auth marking is explicit and per operation: a new public one must be declared.

    Uses the same predicate as the generator, so the two cannot disagree about
    what "public" means.
    """
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    assert public_operations_in(spec) == set(PUBLIC_OPERATIONS)


def test_a_declared_public_method_does_not_unmark_its_siblings() -> None:
    """Publicness is per operation: a path-level rule would leak the POST.

    Uses a throwaway app that mixes a declared-public method with a protected
    one on the same path, because no current route does that. With the old
    path-level rule the whole path was skipped, leaving the POST unmarked.
    """
    from fastapi import Depends, FastAPI
    from fastapi.security import HTTPBearer

    probe = FastAPI()
    # A route using the scheme, so the document has one to point at.
    probe.add_api_route(
        "/api/probe-secure",
        lambda: {"ok": True},
        methods=["GET"],
        dependencies=[Depends(HTTPBearer())],
    )
    # GET /api/health is declared public; a POST on the same path is not.
    probe.add_api_route("/api/health", lambda: {"ok": True}, methods=["GET"])
    probe.add_api_route("/api/health", lambda: {"ok": True}, methods=["POST"])

    spec = build_spec(probe)
    probe_paths = spec["paths"]["/api/health"]
    assert not probe_paths["get"].get("security"), "the declared public method stays open"
    assert probe_paths["post"].get("security"), "its sibling must stay protected"


def test_field_level_public_operations_are_named_with_a_method() -> None:
    """Guard the shape of the declaration so it stays operation-level."""
    assert all(isinstance(entry, tuple) and len(entry) == 2 for entry in PUBLIC_OPERATIONS)
    assert {method for method, _ in PUBLIC_OPERATIONS} <= {"GET", "POST", "PUT", "PATCH", "DELETE"}


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
        if not path.startswith("/api/"):
            # Fail loudly rather than quietly checking the wrong upstream path.
            missing.append(f"{path} is not written with the /api/ prefix we call through")
            continue
        upstream = "/" + path.removeprefix("/api/")
        for method in methods:
            if method.lower() not in {m.lower() for m in live_paths.get(upstream, {})}:
                missing.append(f"{method} {path} (expected {upstream})")

    assert not missing, (
        "openapi/openrag.yaml documents endpoints OpenRAG no longer serves: "
        + json.dumps(missing, ensure_ascii=False)
    )
