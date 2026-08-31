"""Minimal OpenRAG API client.

OpenRAG exposes an HTTP API plus SDKs. This client is intentionally thin:
it centralizes base URL, API key, and common request/error handling so the
ingestion/evaluation scripts can evolve without repeating HTTP code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class OpenRAGError(RuntimeError):
    """Raised when OpenRAG returns an unexpected response."""


class OpenRAGClient:
    """A small synchronous HTTP client for OpenRAG."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = httpx.request(
                method,
                self._url(path),
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise OpenRAGError(f"OpenRAG request failed: {exc}") from exc

        if resp.is_error:
            raise OpenRAGError(f"OpenRAG {method} {path} -> {resp.status_code}: {resp.text[:300]}")

        if resp.content:
            return resp.json()
        return None

    def health(self) -> dict[str, Any]:
        """Check OpenRAG service health."""
        return self._request("GET", "/api/health")

    def chat(self, message: str) -> dict[str, Any]:
        """Send a chat message. Endpoint is a placeholder pending OpenRAG API docs."""
        return self._request("POST", "/api/chat", json={"message": message})

    def search(self, query: str) -> dict[str, Any]:
        """Semantic search. Endpoint is a placeholder pending OpenRAG API docs."""
        return self._request("POST", "/api/search", json={"query": query})

    def upload_document(self, path: Path) -> dict[str, Any]:
        """Upload a document. Endpoint is a placeholder pending OpenRAG API docs."""
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("rb") as fh:
            return self._request(
                "POST",
                "/api/documents",
                files={"file": (path.name, fh)},
            )
