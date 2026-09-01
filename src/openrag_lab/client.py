"""OpenRAG HTTP API client.

This is a thin synchronous client for the public OpenRAG v1 API.
It centralizes base URL, API key, and common request/error handling so the
ingestion/evaluation scripts can evolve without repeating HTTP code.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from openrag_lab.config import get_settings


class OpenRAGError(RuntimeError):
    """Raised when OpenRAG returns an unexpected response."""


class OpenRAGClient:
    """A small synchronous HTTP client for OpenRAG's public API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
        ingest_timeout: float = 600.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.openrag_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.openrag_api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.ingest_timeout = ingest_timeout

        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OpenRAGClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── low-level helpers ──────────────────────────────────────────────

    def _headers(self, multipart: bool = False) -> dict[str, str]:
        headers = {} if multipart else {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        multipart = kwargs.pop("multipart", False)
        try:
            resp = self._http.request(
                method,
                self._url(path),
                headers=self._headers(multipart=multipart),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise OpenRAGError(f"OpenRAG request failed: {exc}") from exc

        if resp.is_error:
            raise OpenRAGError(
                f"OpenRAG {method} {path} -> {resp.status_code}: {resp.text[:500]}"
            )

        if resp.content:
            return resp.json()
        return None

    # ── public endpoints ───────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """Check OpenRAG service health (through the frontend proxy)."""
        return self._request("GET", "/api/health")

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        filter_id: str | None = None,
        rerank: bool = False,
        rerank_model: str | None = None,
        rerank_top_n: int | None = None,
    ) -> dict[str, Any]:
        """Run semantic search against OpenRAG's public v1 API."""
        body: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        if filters:
            body["filters"] = filters
        if filter_id:
            body["filter_id"] = filter_id
        if rerank:
            body["rerank"] = True
            if rerank_model:
                body["rerank_model"] = rerank_model
            if rerank_top_n is not None:
                body["rerank_top_n"] = rerank_top_n
        return self._request("POST", "/api/v1/search", json=body)

    def chat(
        self,
        message: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float = 0.0,
        filter_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat message via OpenRAG's public v1 API."""
        body: dict[str, Any] = {
            "message": message,
            "stream": False,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        if filters:
            body["filters"] = filters
        if filter_id:
            body["filter_id"] = filter_id
        return self._request("POST", "/api/v1/chat", json=body)

    def upload_document(self, path: Path) -> dict[str, Any]:
        """Upload a single document and return the ingestion task response."""
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("rb") as fh:
            return self._request(
                "POST",
                "/api/v1/documents/ingest",
                files={"file": (path.name, fh)},
                data={"replace_duplicates": "true"},
                multipart=True,
            )

    def task_status(self, task_id: str) -> dict[str, Any]:
        """Get the current status of an ingestion task."""
        return self._request("GET", f"/api/v1/tasks/{task_id}")

    def wait_for_task(self, task_id: str) -> dict[str, Any]:
        """Poll an ingestion task until it completes, fails, or times out."""
        elapsed = 0.0
        while elapsed < self.ingest_timeout:
            status = self.task_status(task_id)
            if status.get("status") in ("completed", "failed"):
                return status
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval
        raise OpenRAGError(f"Ingestion task {task_id} timed out after {self.ingest_timeout}s")

    def ingest_file(self, path: Path, wait: bool = True) -> dict[str, Any]:
        """Upload a file and optionally wait for its task to complete."""
        resp = self.upload_document(path)
        task_id = resp.get("task_id")
        if not task_id:
            raise OpenRAGError(f"No task_id returned for {path.name}: {resp!r}")
        if not wait:
            return resp
        return self.wait_for_task(task_id)

    def list_files(self) -> list[dict[str, Any]]:
        """List all ingested files (v1 endpoint, max 500 files)."""
        data = self._request("GET", "/api/v1/files/get_all")
        return data.get("files", [])

    def create_knowledge_filter(
        self,
        name: str,
        *,
        description: str = "",
        query_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a reusable knowledge filter."""
        body = {
            "name": name,
            "description": description,
            "queryData": query_data or {},
        }
        return self._request("POST", "/api/v1/knowledge-filters", json=body)
