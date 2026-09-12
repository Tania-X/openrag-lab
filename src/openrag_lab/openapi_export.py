"""Export the FastAPI app's OpenAPI document as a committed contract artifact.

The app is the source of truth; ``openapi/openrag-lab.yaml`` is a generated
copy so the frontend contract can be reviewed in a diff. ``tests/
test_openapi_artifact.py`` regenerates it and fails when the two disagree, so
the artifact cannot quietly go stale the way a hand-written one does.

Auth marking needs no help here: FastAPI derives ``security`` from the
dependency graph, so every operation guarded by ``get_current_user`` (directly
or through ``require_permission``) comes out marked, and the public three come
out unmarked. The tests verify that against the graph rather than assuming it.

Regenerate with::

    openrag-lab export-openapi
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: ``(METHOD, path)`` pairs expected to need no bearer token
#: (docs/api-contract.md 2.1). This is the declared expectation, asserted
#: against the app's dependency graph and against the generated document by
#: tests/test_openapi_artifact.py — a new public endpoint has to be added here,
#: and that disagreement is what makes the choice visible in review.
PUBLIC_OPERATIONS = frozenset(
    {
        ("GET", "/api/health"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
    }
)

SCHEME_NAME = "HTTPBearer"

DEFAULT_OUTPUT = Path("openapi/openrag-lab.yaml")

HEADER = """\
# OpenRAG Lab 自研后端契约（OpenAPI 3.1）
#
# 本文件由代码生成，请勿手工修改：
#   openrag-lab export-openapi
# 源：src/openrag_lab/api/main.py 注册的全部路由与 interfaces/schemas 的模型。
# 一致性由 tests/test_openapi_artifact.py 保证（与代码不一致时 CI 失败）。
#
# 除 /api/health、/api/auth/register、/api/auth/login 外，
# 其余操作都需要 Authorization: Bearer <token>（见下方 security）。
"""


def build_spec(app: Any | None = None) -> dict[str, Any]:
    """Return the app's OpenAPI document with auth requirements marked."""
    if app is None:
        from openrag_lab.api.main import app as fastapi_app

        app = fastapi_app

    spec = app.openapi()
    if SCHEME_NAME not in spec.get("components", {}).get("securitySchemes", {}):
        raise RuntimeError(
            "the app exposes no HTTPBearer scheme; auth marking would be "
            "unverifiable, so refuse to emit a contract"
        )
    return spec


def public_operations_in(spec: dict[str, Any]) -> set[tuple[str, str]]:
    """Return the ``(METHOD, path)`` pairs the document leaves unauthenticated."""
    return {
        (method.upper(), path)
        for path, operations in spec.get("paths", {}).items()
        for method, operation in operations.items()
        if isinstance(operation, dict) and not operation.get("security")
    }


def render_yaml(spec: dict[str, Any]) -> str:
    """Serialise the spec deterministically so diffs stay readable."""
    body = yaml.safe_dump(
        spec,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
        width=100,
    )
    return HEADER + body


def export_openapi(output: Path = DEFAULT_OUTPUT, app: Any | None = None) -> Path:
    """Write the generated contract to ``output`` and return the path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_yaml(build_spec(app)), encoding="utf-8")
    return output


def documented_operations() -> dict[str, set[str]]:
    """Read the committed upstream-subset contract as ``{path: {methods}}``.

    Used by the consistency test that checks ``openapi/openrag.yaml`` against a
    running OpenRAG deployment.
    """
    spec = yaml.safe_load((DEFAULT_OUTPUT.parent / "openrag.yaml").read_text("utf-8"))
    return {
        path: {method.upper() for method in operations}
        for path, operations in (spec.get("paths") or {}).items()
    }
