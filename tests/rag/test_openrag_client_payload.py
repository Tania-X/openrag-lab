"""Tests for the HTTP body the gateway actually sends.

The endpoint tests stop at the gateway and the gateway tests use a fake
client, so nothing so far proved what leaves the process. This closes that
gap with a mocked transport: the tenant boundary must be present in the
request body even when the tenant has no documents, because OpenRAG reads a
missing ``filters`` key as "do not filter at all".
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from openrag_lab.infrastructure.openrag.openrag_port_impl import OpenRAGGateway


@pytest.fixture
def sent_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Capture the requests the real client issues."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": [], "response": "ok"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("openrag_lab.client.httpx.Client", patched_client)
    return captured


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def test_search_sends_an_empty_scope_instead_of_dropping_filters(
    sent_requests: list[httpx.Request],
) -> None:
    """An empty data_sources list must survive as a key, not vanish."""
    gateway = OpenRAGGateway(base_url="http://openrag.test")

    gateway.search(
        api_key="orag_tenant_key",
        query="报表",
        filters={"data_sources": []},
        limit=3,
        score_threshold=0.0,
    )

    request = sent_requests[-1]
    assert str(request.url) == "http://openrag.test/api/v1/search"
    assert request.headers["x-api-key"] == "orag_tenant_key"
    assert _body(request)["filters"] == {"data_sources": []}


def test_chat_sends_an_empty_scope_instead_of_dropping_filters(
    sent_requests: list[httpx.Request],
) -> None:
    gateway = OpenRAGGateway(base_url="http://openrag.test")

    gateway.chat(
        api_key="orag_tenant_key",
        message="投诉时限?",
        filters={"data_sources": []},
        limit=3,
        score_threshold=0.0,
    )

    request = sent_requests[-1]
    assert str(request.url) == "http://openrag.test/api/v1/chat"
    assert _body(request)["filters"] == {"data_sources": []}


def test_search_sends_the_registered_filenames(
    sent_requests: list[httpx.Request],
) -> None:
    gateway = OpenRAGGateway(base_url="http://openrag.test")

    gateway.search(
        api_key="k",
        query="报表",
        filters={"data_sources": ["acme/a.md", "acme/b.md"]},
        limit=3,
        score_threshold=0.0,
        rerank=True,
    )

    body = _body(sent_requests[-1])
    assert body["filters"] == {"data_sources": ["acme/a.md", "acme/b.md"]}
    assert body["rerank"] is True
    assert body["query"] == "报表"
