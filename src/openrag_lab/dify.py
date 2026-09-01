"""Minimal Dify knowledge-base retrieval client for comparison runs."""

from __future__ import annotations

from typing import Any

import httpx

from openrag_lab.config import get_settings


class DifyError(RuntimeError):
    """Raised when Dify returns an unexpected response."""


class DifyClient:
    """A small synchronous client for Dify dataset retrieval."""

    def __init__(
        self,
        base_url: str | None = None,
        dataset_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.dify_base_url).rstrip("/")
        self.dataset_id = dataset_id or settings.dify_dataset_id
        self.api_key = api_key or settings.dify_dataset_api_key
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DifyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
        rerank: bool = False,
        rerank_provider: str | None = None,
        rerank_model: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call Dify's knowledge-base retrieve API."""
        settings = get_settings()
        retrieval_model: dict[str, Any] = {
            "search_method": "hybrid_search",
            "reranking_enable": rerank,
            "top_k": top_k,
            "score_threshold_enabled": False,
        }

        if rerank:
            retrieval_model["reranking_mode"] = "reranking_model"
            retrieval_model["reranking_model"] = {
                "reranking_provider_name": rerank_provider
                or settings.dify_rerank_provider,
                "reranking_model_name": rerank_model or settings.dify_rerank_model,
            }

        if metadata:
            retrieval_model["metadata_filtering_conditions"] = {
                "logical_operator": "and",
                "conditions": [
                    {
                        "name": name,
                        "comparison_operator": "is",
                        "value": value,
                    }
                    for name, value in metadata.items()
                    if value
                ],
            }

        body = {"query": query, "retrieval_model": retrieval_model}

        try:
            resp = self._http.post(
                f"{self.base_url}/v1/datasets/{self.dataset_id}/retrieve",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as exc:
            raise DifyError(f"Dify request failed: {exc}") from exc

        if resp.is_error:
            raise DifyError(
                f"Dify retrieve -> {resp.status_code}: {resp.text[:500]}"
            )
        return resp.json()
