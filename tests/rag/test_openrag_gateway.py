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
from pathlib import Path
from typing import Any

import pytest

from openrag_lab.client import OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.rag.ports import RagOutcomeUnknownError
from openrag_lab.infrastructure.openrag.openrag_port_impl import OpenRAGGateway


class FakeClient:
    """Stands in for OpenRAGClient and records how it was used."""

    instances: list[FakeClient] = []

    #: Set by a test to steer the next delete/ingest call. Class-level because
    #: the gateway builds its client lazily, inside the call under test.
    behavior: dict[str, Any] = {}

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float | None = None,
        ingest_timeout: float | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
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

    def list_files(self) -> list[dict[str, Any]]:
        self.calls.append(("list_files", {}))
        return list(FakeClient.behavior.get("remote_files", []))

    def delete_document(self, filename: str) -> Any:
        self.calls.append(("delete_document", {"filename": filename}))
        if "delete_error" in FakeClient.behavior:
            raise FakeClient.behavior["delete_error"]
        return FakeClient.behavior.get(
            "delete_payload", {"success": True, "deleted_chunks": 3}
        )

    def ingest_file(
        self, path: Any, wait: bool = True, filename: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("ingest_file", {"path": str(path), "wait": wait, "filename": filename}))
        if "ingest_error" in FakeClient.behavior:
            raise FakeClient.behavior["ingest_error"]
        return {"status": "completed"}


@pytest.fixture(autouse=True)
def _reset_instances() -> Iterator[None]:
    """Clear the shared instance list *after* each test, not before it."""
    yield
    FakeClient.instances = []
    FakeClient.behavior = {}


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


def test_concurrent_first_calls_share_one_live_client() -> None:
    """冷启动竞争: 调用者必须收敛到**同一个存活 client**, 多建的落选者要立即关闭。

    构建在锁外进行(评审第 2 轮修订), 所以竞争允许"多建一个再丢弃"; 真正的不变量是
    "缓存里只有一个、所有调用都用它、落选者不泄漏"。旧实现锁内构建能保证只建一个,
    代价是把其它租户的首次调用一起堵住(见下一条测试)。
    """
    build_lock = threading.Lock()
    built: list[str] = []

    class SlowClient(FakeClient):
        def __init__(
            self,
            base_url: str,
            api_key: str,
            timeout: float | None = None,
            ingest_timeout: float | None = None,
        ) -> None:
            with build_lock:
                built.append(api_key)
            time.sleep(0.05)  # 拉长构建窗口, 让竞争真的发生
            super().__init__(base_url, api_key, timeout=timeout, ingest_timeout=ingest_timeout)

    gateway = OpenRAGGateway(client_factory=SlowClient)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _search(gateway, "tenant-a"), range(8)))

    live = [c for c in SlowClient.instances if not c.closed]
    assert len(live) == 1, f"应只剩一个存活 client, 实际 {len(live)}"
    surviving = live[0]
    # 落选者(如果竞争发生了)必须被关闭, 不能泄漏
    assert all(c is surviving or c.closed for c in SlowClient.instances)
    # 8 次检索全部落在同一个存活 client 上
    assert sum(len(c.calls) for c in SlowClient.instances) == 8
    assert len(surviving.calls) == 8
    gateway.close()


def test_building_one_tenant_does_not_block_another() -> None:
    """构造必须在锁外: 一个租户的慢构建不能堵住另一个租户的冷启动。

    修复前 `_acquire` 在 `_clients_lock` 内调用 `_build_client`; 实测构造一个
    `httpx.Client` 约 12ms(SSL 上下文/证书), 自定义 factory 可能更慢 —— 那段时间里
    所有租户的**首次调用**(以及借用计数)都得排队。
    """
    started = threading.Event()
    release = threading.Event()

    class BlockingBuild(FakeClient):
        def __init__(
            self,
            base_url: str,
            api_key: str,
            timeout: float | None = None,
            ingest_timeout: float | None = None,
        ) -> None:
            if api_key == "tenant-slow":
                started.set()
                release.wait()  # 一直卡住, 直到本测试显式放行
            super().__init__(base_url, api_key, timeout=timeout, ingest_timeout=ingest_timeout)

    gateway = OpenRAGGateway(client_factory=BlockingBuild)
    slow = threading.Thread(target=lambda: _search(gateway, "tenant-slow"), daemon=True)
    slow.start()
    assert started.wait(timeout=5), "慢构建线程没能开始"

    fast_done = threading.Event()

    def fast_call() -> None:
        _search(gateway, "tenant-fast")
        fast_done.set()

    threading.Thread(target=fast_call, daemon=True).start()
    try:
        # 关键断言: **限时**完成。锁内构建时这里会等到慢构建放行(旧行为), 因此会失败。
        assert fast_done.wait(timeout=2), "另一个租户的冷启动被慢构建堵住了(构建必须在锁外)"
        assert gateway._clients["tenant-fast"].client.api_key == "tenant-fast"
    finally:
        release.set()
        slow.join(timeout=5)
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


def test_a_zero_ceiling_does_not_hand_out_a_closed_client() -> None:
    """评审第 3 轮(4 级)回归: 上限被钳到 1, 且新条目先计数再参与淘汰。

    修复前顺序是"入缓存 → 淘汰 → refs += 1": 上限为 0 时新条目正是唯一淘汰候选,
    会被 pop + close(refs 仍为 0), 然后才 +1 并借给调用方 —— 调用方拿到已关闭的 client,
    归还时还会重复关闭。
    """
    gateway = OpenRAGGateway(client_factory=FakeClient, max_cached_clients=0)
    assert gateway._max_cached_clients == 1, "上限应被钳制为至少 1"

    _search(gateway, "tenant-a")

    client = FakeClient.instances[-1]
    assert client.closed is False, "借出的 client 不能被关闭"
    assert len(client.calls) == 1, "调用必须真的发出去"
    gateway.close()
    assert client.closed is True


# ── P2: 删除/上传的结局分类 ────────────────────────────────────────────────
# 端口契约有三种结局, 而它们的区别正是"能不能相信这一行"的全部依据:
#   * 正常返回        = 有定论(删掉了, 或远端本来就没有)
#   * RagOutcomeUnknownError = 没有定论(超时/连接断)
#   * 其它异常        = 明确的拒绝(远端答了, 说不)
# 这些分类只能在适配器里做(它知道 OpenRAG 的状态码约定), 所以必须在这一层测。


def test_a_missing_document_is_a_settled_delete_not_a_failure() -> None:
    """OpenRAG 对"没有匹配的分块"回 404 —— 那是"已经没有了", 不是失败。"""
    FakeClient.behavior = {
        "delete_error": OpenRAGError(
            "OpenRAG DELETE /api/v1/documents -> 404",
            status_code=404,
            payload={"success": False, "deleted_chunks": 0},
        )
    }
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        result = gateway.delete_document(api_key="k", stored_filename="acme/gone.md")
    finally:
        gateway.close()

    assert result == {"deleted_chunks": 0, "already_absent": True}


def test_a_bare_404_is_not_swallowed() -> None:
    """反向守卫: 形状不对的 404(例如路由写错了)必须照旧抛错。

    只看状态码会把"我调错了地址"读成"文档已经没了" —— 那会让删除静默地什么都没做。
    """
    FakeClient.behavior = {
        "delete_error": OpenRAGError("not found", status_code=404, payload={"detail": "no route"})
    }
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        with pytest.raises(OpenRAGError):
            gateway.delete_document(api_key="k", stored_filename="acme/x.md")
    finally:
        gateway.close()


def test_a_transport_failure_on_delete_means_no_verdict() -> None:
    """超时/连接断: 不知道远端有没有删掉 → 端口返回"无定论"。"""
    FakeClient.behavior = {"delete_error": OpenRAGError("OpenRAG request failed: timeout")}
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        with pytest.raises(RagOutcomeUnknownError) as caught:
            gateway.delete_document(api_key="k", stored_filename="acme/x.md")
    finally:
        gateway.close()

    assert caught.value.operation == "delete"


def test_a_server_error_is_a_refusal_not_an_unknown_outcome() -> None:
    """远端答了 500: 那是"明确拒绝", 不是"不知道" —— 两者对状态机是不同的迁移。

    混为一谈会让一个其实没删掉的行被记成"未确认的删除", 而它真正需要的是重试。
    """
    FakeClient.behavior = {
        "delete_error": OpenRAGError("boom", status_code=500, payload={"detail": "internal"})
    }
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        with pytest.raises(OpenRAGError) as caught:
            gateway.delete_document(api_key="k", stored_filename="acme/x.md")
    finally:
        gateway.close()

    assert not isinstance(caught.value, RagOutcomeUnknownError)


def test_a_transport_failure_on_ingest_means_no_verdict() -> None:
    """上传也一样: 远端可能已经写入, 只是没答复。"""
    FakeClient.behavior = {"ingest_error": OpenRAGError("OpenRAG request failed: read timeout")}
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        with pytest.raises(RagOutcomeUnknownError) as caught:
            gateway.ingest_document(
                api_key="k", stored_filename="acme/x.md", path=Path("/tmp/x.md")
            )
    finally:
        gateway.close()

    assert caught.value.operation == "ingest"


def test_a_successful_delete_normalises_to_the_port_shape() -> None:
    """成功路径也归一化: 调用方只认 deleted_chunks/already_absent 两个字段。"""
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        result = gateway.delete_document(api_key="k", stored_filename="acme/x.md")
    finally:
        gateway.close()

    assert result == {"deleted_chunks": 3, "already_absent": False}


def test_a_successful_delete_without_a_body_still_settles() -> None:
    """2xx 但没 body: 仍然是"删掉了", 只是块数未知 —— 不能编一个数字。"""
    FakeClient.behavior = {"delete_payload": None}
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        result = gateway.delete_document(api_key="k", stored_filename="acme/x.md")
    finally:
        gateway.close()

    assert result == {"deleted_chunks": 0, "already_absent": False}


def test_list_document_filenames_returns_what_openrag_stores() -> None:
    """对账要一次拿到远端全量名单, 而不是逐个探活。"""
    FakeClient.behavior = {
        "remote_files": [
            {"filename": "acme/a.md", "document_id": "1"},
            {"filename": "acme/b.md", "document_id": "2"},
            {"document_id": "3"},  # 没有文件名的条目要跳过, 不能变成 None 混进名单
        ]
    }
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        names = gateway.list_document_filenames(api_key="k")
    finally:
        gateway.close()

    assert names == ["acme/a.md", "acme/b.md"]


def test_the_per_request_timeout_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求超时是可配的, 因为对账的"删除卡住"阈值由它推导 —— 阈值必须来自
    运维看得见、改得动的数字, 而不是客户端里的一个字面量。"""
    monkeypatch.setattr(
        get_settings(), "openrag_request_timeout_seconds", 42.0, raising=False
    )
    gateway = OpenRAGGateway(client_factory=FakeClient, base_url="http://openrag.test")
    try:
        gateway.chat(
            api_key="k", message="q", filters={"data_sources": []}, limit=1, score_threshold=0.0
        )
    finally:
        gateway.close()

    assert FakeClient.instances[-1].timeout == 42.0
