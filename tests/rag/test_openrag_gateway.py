"""Tests for the OpenRAG gateway adapter.

The endpoint tests replace the gateway with a fake, so they cannot catch a
mistake in the adapter itself (wrong address, lost filters, leaked client).
These tests cover that layer with a fake *client*.

Client lifecycle is part of the contract now: a client is built once per API
key, kept open for reuse (it owns a connection pool) and closed when the
gateway closes. Tests below pin reuse, the cache ceiling and shutdown.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from openrag_lab.config import get_settings
from openrag_lab.infrastructure.openrag.openrag_port_impl import OpenRAGGateway


class FakeClient:
    """Stands in for OpenRAGClient and records how it was used."""

    instances: list[FakeClient] = []

    def __init__(self, base_url: str, api_key: str, ingest_timeout: float | None = None) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.ingest_timeout = ingest_timeout
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
    # Reused across calls, so it stays open; the gateway closes it at shutdown.
    assert client.closed is False
    gateway.close()
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
    assert client.closed is False
    gateway.close()
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


def test_the_gateway_bounds_how_long_an_ingest_may_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploading holds a worker thread, so the wait must be bounded."""
    monkeypatch.setattr(
        get_settings(), "upload_ingest_timeout_seconds", 42.0, raising=False
    )
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    gateway.chat(
        api_key="k", message="q", filters={"data_sources": []}, limit=1, score_threshold=0.0
    )
    assert FakeClient.instances[-1].ingest_timeout == 42.0


def test_an_explicit_ingest_timeout_wins_over_settings() -> None:
    gateway = OpenRAGGateway(
        client_factory=FakeClient, base_url="http://openrag.test", ingest_timeout=7.5
    )
    gateway.chat(
        api_key="k", message="q", filters={"data_sources": []}, limit=1, score_threshold=0.0
    )
    assert FakeClient.instances[-1].ingest_timeout == 7.5


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


# ── client lifecycle: reuse, ceiling, shutdown ─────────────────────────────


def _search(gateway: OpenRAGGateway, api_key: str) -> None:
    gateway.search(
        api_key=api_key,
        query="q",
        filters={"data_sources": []},
        limit=1,
        score_threshold=0.0,
    )


def test_calls_with_the_same_key_reuse_one_client() -> None:
    """The pool is the point: repeat calls must not rebuild the client."""
    gateway = OpenRAGGateway(client_factory=FakeClient)
    _search(gateway, "tenant-a")
    _search(gateway, "tenant-a")
    _search(gateway, "tenant-a")

    assert len(FakeClient.instances) == 1
    gateway.close()


def test_different_tenants_never_share_a_client() -> None:
    """The API key is bound at construction, so it is the cache key too."""
    gateway = OpenRAGGateway(client_factory=FakeClient)
    _search(gateway, "tenant-a")
    _search(gateway, "tenant-b")

    assert [c.api_key for c in FakeClient.instances] == ["tenant-a", "tenant-b"]
    gateway.close()


def test_close_releases_every_cached_client_and_is_idempotent() -> None:
    gateway = OpenRAGGateway(client_factory=FakeClient)
    _search(gateway, "tenant-a")
    _search(gateway, "tenant-b")

    gateway.close()
    assert all(c.closed for c in FakeClient.instances)

    gateway.close()  # second call must not raise
    assert all(c.closed for c in FakeClient.instances)


def test_the_cache_ceiling_evicts_the_oldest_client() -> None:
    """Sockets are bounded: past the ceiling the oldest client is closed."""
    gateway = OpenRAGGateway(client_factory=FakeClient, max_cached_clients=2)
    _search(gateway, "tenant-a")
    _search(gateway, "tenant-b")
    _search(gateway, "tenant-c")

    first, second, third = FakeClient.instances
    assert first.closed is True          # evicted, and its pool released
    assert second.closed is False
    assert third.closed is False

    _search(gateway, "tenant-a")         # rebuilt on demand
    assert len(FakeClient.instances) == 4
    gateway.close()


def test_concurrent_first_calls_build_one_client_per_key() -> None:
    """The gateway runs on worker threads: a cold key must not build twice."""
    built: list[str] = []
    build_lock = threading.Lock()

    class SlowClient(FakeClient):
        def __init__(self, base_url: str, api_key: str, ingest_timeout: float | None = None) -> None:
            with build_lock:
                built.append(api_key)
            time.sleep(0.05)  # widen the window two threads could race in
            super().__init__(base_url, api_key, ingest_timeout)

    gateway = OpenRAGGateway(client_factory=SlowClient)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _search(gateway, "tenant-a"), range(8)))

    assert built == ["tenant-a"], f"expected one build per key, got {built}"
    gateway.close()


def test_eviction_does_not_close_a_client_that_is_in_use() -> None:
    """评审第 1 轮(4 级)回归: 淘汰不能打断在途请求。

    修复前 `_client` 的快路径无锁返回引用, 另一个线程淘汰同一条目时直接 close(),
    那个正在 search/ingest 的请求就会失败; 用第 65 个 key 时必然触发淘汰。
    现在条目带借用计数: 淘汰只标记, 最后一个借用结束时才关闭。
    """
    gateway = OpenRAGGateway(client_factory=FakeClient, max_cached_clients=1)

    with gateway._borrow("tenant-a") as held:
        _search(gateway, "tenant-b")           # 挤掉 tenant-a, 但它正被借用
        assert held.closed is False, "在途请求的客户端不能被关闭"
    assert held.closed is True, "借用结束后才关闭被淘汰的客户端"


def test_close_defers_until_an_in_flight_borrow_ends() -> None:
    """关闭进程时同样不能打断在途请求; 借用结束时才真正关闭。"""
    gateway = OpenRAGGateway(client_factory=FakeClient)

    with gateway._borrow("tenant-a") as held:
        gateway.close()
        assert held.closed is False
    assert held.closed is True


def test_borrows_are_released_even_when_the_call_raises() -> None:
    """借出必须靠 with 保证归还: 调用抛异常也不能让计数泄漏(否则该 client 永不关闭)。"""
    class Boom(FakeClient):
        def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            if self.api_key == "tenant-a":       # 只让第一个租户失败
                raise RuntimeError("boom")
            return super().search(query, **kwargs)

    gateway = OpenRAGGateway(client_factory=Boom, max_cached_clients=1)
    with pytest.raises(RuntimeError):
        _search(gateway, "tenant-a")

    _search(gateway, "tenant-b")               # 挤掉 tenant-a
    assert Boom.instances[0].closed is True, "异常路径也必须归还借用"
