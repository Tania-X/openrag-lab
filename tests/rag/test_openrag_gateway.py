"""Tests for the OpenRAG gateway adapter.

The endpoint tests replace the gateway with a fake, so they cannot catch a
mistake in the adapter itself (wrong address, lost filters, leaked client).
These tests cover that layer with a fake *client*.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from openrag_lab.config import get_settings
from openrag_lab.infrastructure.openrag.openrag_port_impl import OpenRAGGateway


class FakeClient:
    """Stands in for OpenRAGClient and records how it was used."""

    instances: list[FakeClient] = []

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        FakeClient.instances.append(self)

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search", {"query": query, **kwargs}))
        return {"results": []}

    def chat(self, message: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat", {"message": message, **kwargs}))
        return {"response": "ok"}


@pytest.fixture(autouse=True)
def _reset_instances() -> Iterator[None]:
    """Clear the shared instance list *after* each test, not before it."""
    yield
    FakeClient.instances = []


def test_search_targets_the_configured_openrag_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address must come from configuration, not from a hardcoded default."""
    monkeypatch.setattr(
        get_settings(), "openrag_base_url", "http://openrag.internal:9999", raising=False
    )
    gateway = OpenRAGGateway(client_factory=FakeClient)

    gateway.search(
        api_key="orag_tenant_key",
        query="报表",
        filters={"data_sources": ["acme/report.md"]},
        limit=5,
        score_threshold=0.0,
    )

    client = FakeClient.instances[-1]
    assert client.base_url == "http://openrag.internal:9999"
    assert client.api_key == "orag_tenant_key"
    assert client.closed is True


def test_chat_targets_the_configured_openrag_instance() -> None:
    gateway = OpenRAGGateway(client_factory=FakeClient)
    gateway.chat(
        api_key="orag_tenant_key",
        message="投诉时限?",
        filters={"data_sources": []},
        limit=5,
        score_threshold=0.0,
    )

    client = FakeClient.instances[-1]
    assert client.base_url == get_settings().openrag_base_url
    assert client.api_key == "orag_tenant_key"
    assert client.closed is True


def test_search_forwards_the_server_built_filters() -> None:
    gateway = OpenRAGGateway(client_factory=FakeClient)
    filters = {"data_sources": ["acme/a.md", "acme/b.md"]}

    gateway.search(
        api_key="k",
        query="q",
        filters=filters,
        limit=3,
        score_threshold=0.5,
        rerank=True,
        rerank_model="BAAI/bge-reranker-v2-m3",
        rerank_top_n=5,
    )

    kind, call = FakeClient.instances[-1].calls[-1]
    assert kind == "search"
    assert call["filters"] == filters
    assert call["limit"] == 3
    assert call["score_threshold"] == 0.5
    assert call["rerank"] is True
    assert call["rerank_model"] == "BAAI/bge-reranker-v2-m3"
    assert call["rerank_top_n"] == 5


def test_an_explicit_base_url_wins_over_settings() -> None:
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://explicit:1234")
    gateway.search(
        api_key="k",
        query="q",
        filters={"data_sources": []},
        limit=1,
        score_threshold=0.0,
    )
    assert FakeClient.instances[-1].base_url == "http://explicit:1234"
