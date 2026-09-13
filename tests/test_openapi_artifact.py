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


def _api_routes(routes):
    """Yield APIRoute objects, unwrapping FastAPI's included-router wrappers.

    With FastAPI 0.141 ``app.include_router`` does not flatten: ``app.routes``
    holds 8 ``_IncludedRouter`` wrappers plus the 4 built-in ``Route`` objects and
    no top-level ``APIRoute`` at all, so the unwrap is required. It is kept
    defensive rather than deleting it, but a future internals change must fail
    loudly — hence the explicit check in ``authenticated_operations``.
    """
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        inner = getattr(route, "original_router", None) or getattr(route, "router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)


def _dependency_calls(dependant, seen: set | None = None) -> set:
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        seen.add(sub.call)
        _dependency_calls(sub, seen)
    return seen


def authenticated_operations(app) -> set[tuple[str, str]]:
    """Operations whose dependency graph actually pulls in authentication.

    Derived from the wiring, not from the contract's own declaration, so the
    two can be compared against each other.
    """
    from openrag_lab.interfaces.api.deps import get_current_user

    routes = list(_api_routes(app.routes))
    if not routes:
        raise AssertionError(
            "no APIRoute could be reached from app.routes: FastAPI's route "
            "wrapping changed, so this introspection needs updating"
        )
    found: set[tuple[str, str]] = set()
    for route in routes:
        if get_current_user in _dependency_calls(route.dependant):
            found.update((method.upper(), route.path) for method in route.methods)
    return found


def all_operations(app) -> set[tuple[str, str]]:
    return {
        (method.upper(), route.path)
        for route in _api_routes(app.routes)
        for method in route.methods
    }


def marking_mismatches(spec: dict) -> set[tuple[str, str]]:
    """Operations whose documented auth marking disagrees with the real graph."""
    expected_public = all_operations(app) - authenticated_operations(app)
    return public_operations_in(spec) ^ expected_public


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


def test_route_introspection_reaches_the_app_routes() -> None:
    """Guards the unwrapping above: an internals change must not empty it."""
    routes = list(_api_routes(app.routes))
    assert len(routes) >= 13, f"only found {len(routes)} routes"
    assert {"/api/health", "/api/search", "/api/documents"} <= {r.path for r in routes}


def test_marking_agrees_with_the_real_dependency_graph() -> None:
    """The document's auth marking must match the app's actual wiring.

    Deliberately not derived from PUBLIC_OPERATIONS: if the generator ever
    failed to mark a protected operation, both the declaration and the document
    would change together and a self-referential assertion would still pass.
    """
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    assert marking_mismatches(spec) == set()


def test_the_declaration_names_exactly_the_public_operations() -> None:
    """The public set is explicit, and it is what the graph says is public."""
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    declared = set(PUBLIC_OPERATIONS)
    assert public_operations_in(spec) == declared
    assert declared == all_operations(app) - authenticated_operations(app)


def test_the_marking_check_notices_a_protected_operation_left_open() -> None:
    """Strength check: the comparison above is not vacuous."""
    spec = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    spec["paths"]["/api/auth/me"]["get"].pop("security", None)

    mismatches = marking_mismatches(spec)
    assert ("GET", "/api/auth/me") in mismatches


def test_the_public_declaration_matches_the_graph_per_operation() -> None:
    """A mixed path cannot smuggle a protected method into the public set."""
    documented = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    for method, path in PUBLIC_OPERATIONS:
        operation = documented["paths"][path][method.lower()]
        assert not operation.get("security"), f"{method} {path} is declared public"
    assert all(len(entry) == 2 for entry in PUBLIC_OPERATIONS)


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
    # Scheme-aware default: assuming 80 for https would make the connection fail
    # and silently skip this check on exactly the deployments that need it.
    default_port = 443 if parsed.scheme == "https" else 80
    host, port = parsed.hostname, parsed.port or default_port
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

    # A reachable port does not mean a usable spec endpoint. Without these two
    # guards a 404/HTML answer would be parsed into a dict with no "paths" and
    # every documented endpoint would then be reported as missing - a failure
    # that hides its own cause.
    response = httpx.get(url, timeout=10)
    if response.status_code != 200:
        pytest.skip(
            f"OpenRAG spec endpoint answered {response.status_code}; "
            "the live contract check is inconclusive"
        )
    try:
        live = response.json()
    except ValueError:
        pytest.skip(f"OpenRAG spec endpoint returned no JSON: {url}")

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


def test_documented_operations_ignores_path_level_keys(tmp_path: Path) -> None:
    """OpenAPI allows non-method keys at path level; they are not operations."""
    contract = tmp_path / "upstream.yaml"
    contract.write_text(
        """
openapi: 3.0.3
info: {title: probe, version: "0"}
paths:
  /api/v1/x:
    summary: not an operation
    parameters:
      - name: q
        in: query
    get:
      responses: {"200": {description: ok}}
""",
        encoding="utf-8",
    )

    assert documented_operations(contract) == {"/api/v1/x": {"GET"}}
