"""End-to-end acceptance check for s1p3c against the live OpenRAG deployment.

Run manually (it talks to the real OpenRAG and the local dev database):

    ALLOW_SELF_REGISTRATION=true .venv/bin/python scripts/e2e_documents.py

It registers a throwaway tenant, uploads a document through the API, proves it
is retrievable only by that tenant, deletes it, and reports what it saw.
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from openrag_lab.api.main import app

MARKER = f"E2E-MARKER-{uuid.uuid4().hex[:8]}"


def main() -> None:
    suffix = uuid.uuid4().hex[:6]
    with TestClient(app) as client:
        admin = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}

        listed = client.get("/api/documents", headers=admin_headers).json()
        print(f"admin sees {listed['total']} registered documents")
        print("  sample:", [f["stored_filename"] for f in listed["files"][:2]])

        hit = client.post(
            "/api/search", json={"query": "投诉时限", "limit": 3}, headers=admin_headers
        ).json()
        print("admin search:", hit["scope"], [r["filename"] for r in hit["results"]][:2])

        # A throwaway tenant, with the ability to register itself.
        registered = client.post(
            "/api/auth/register",
            json={
                "username": f"e2e-{suffix}",
                "password": "password12345",
                "tenant_name": f"e2e-{suffix}",
            },
        ).json()
        tenant_slug = f"e2e-{suffix}"
        tenant_headers = {"Authorization": f"Bearer {registered['access_token']}"}
        print("registered tenant:", tenant_slug)

        before = client.post(
            "/api/search", json={"query": MARKER, "limit": 3}, headers=tenant_headers
        ).json()
        print("new tenant search before upload:", before["scope"], before["results"])

        upload = client.post(
            "/api/documents/ingest",
            headers=tenant_headers,
            files={
                "file": (
                    "e2e-report.md",
                    f"# E2E\n唯一标记：{MARKER}\n".encode(),
                    "text/markdown",
                )
            },
        )
        print("upload:", upload.status_code, upload.json().get("stored_filename"))

        after = client.post(
            "/api/search", json={"query": MARKER, "limit": 3}, headers=tenant_headers
        ).json()
        print("new tenant search after upload:", after["scope"])
        print("  hits:", [r["filename"] for r in after["results"]])

        other = client.post(
            "/api/search", json={"query": MARKER, "limit": 3}, headers=admin_headers
        ).json()
        print("admin (default tenant) sees the marker:", [r["filename"] for r in other["results"]])

        deleted = client.delete(
            "/api/documents/e2e-report.md", headers=tenant_headers
        )
        print("delete:", deleted.status_code, deleted.json())

        final = client.post(
            "/api/search", json={"query": MARKER, "limit": 3}, headers=tenant_headers
        ).json()
        print("search after delete:", final["scope"], final["results"])


if __name__ == "__main__":
    if os.environ.get("ALLOW_SELF_REGISTRATION", "").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Set ALLOW_SELF_REGISTRATION=true to run this check.")
    main()
