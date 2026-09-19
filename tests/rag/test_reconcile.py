"""Reconciliation report tests (design §7, stage P3).

This stage is read-only, so most of what can go wrong is a *judgement*: which
rows deserve attention, whether a remote name is really a ghost, whether an
unreadable remote was mistaken for an empty one. Every one of those is a pure
function here, so they are tested directly — no database, no network — and the
service wiring is tested with fakes, including a guard that it never writes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openrag_lab.application.rag.reconcile import (
    CATEGORY_FAILED,
    CATEGORY_STUCK_DELETE,
    CATEGORY_STUCK_UPLOAD,
    CATEGORY_UNCONFIRMED_DELETE,
    INDEXING_STALE_FACTOR,
    Finding,
    ReconcileReport,
    ReconcileService,
    RemoteComparison,
    RemoteUnavailable,
    StalenessRules,
    classify_registry,
    compare_remote,
    render_report,
)
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Document, Tenant, User
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlTenantRepository,
    SqlUserRepository,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
RULES = StalenessRules(indexing_seconds=600.0, deleting_seconds=120.0)
TENANT = TenantId("t-acme")


def _document(
    name: str,
    status: DocumentStatus,
    *,
    age_seconds: float = 0.0,
    reason: str | None = None,
    outcome_unknown: bool = False,
) -> Document:
    document = Document(
        id=DocumentId(f"doc-{name}"),
        tenant_id=TENANT,
        stored_filename=f"acme/{name}",
        display_name=name,
        uploaded_by=UserId("u-alice"),
        status=status,
        status_reason=reason,
        remote_outcome_unknown=outcome_unknown,
    )
    document.updated_at = NOW - timedelta(seconds=age_seconds)
    return document


def _classify(rows: list[Document]) -> list[Finding]:
    return classify_registry(rows, tenant_slug="acme", now=NOW, rules=RULES)


# ── 阈值必须从它守护的超时推导 ──────────────────────────────────────────────


def test_thresholds_are_derived_from_the_timeouts_they_guard() -> None:
    """阈值不是手写的整数: 改了超时, 阈值必须跟着走。

    `INDEXING` 的系数是 2 而不是"经验值": 意图行在拿到限流器**之前**就提交了,
    所以行的年龄 = 排队时间 + 一次完整入库等待。
    """
    rules = StalenessRules.derive(
        ingest_timeout_seconds=300.0, request_timeout_seconds=60.0
    )
    assert rules.indexing_seconds == 300.0 * INDEXING_STALE_FACTOR == 600.0
    assert rules.deleting_seconds == 120.0

    moved = StalenessRules.derive(
        ingest_timeout_seconds=900.0, request_timeout_seconds=30.0
    )
    assert (moved.indexing_seconds, moved.deleting_seconds) == (1800.0, 60.0)


# ── 分类: 只有 unsettled 的行才可能成为 finding ────────────────────────────


def test_a_healthy_upload_in_flight_is_not_reported() -> None:
    """假阳性在这里最贵: 动一个还在正常上传的行可能把半写文档晋升成可用。"""
    assert _classify([_document("a.md", DocumentStatus.INDEXING, age_seconds=599)]) == []


def test_a_stuck_upload_is_reported_with_its_own_action() -> None:
    findings = _classify([_document("slow.md", DocumentStatus.INDEXING, age_seconds=601)])
    assert [f.category for f in findings] == [CATEGORY_STUCK_UPLOAD]
    assert "probe" in findings[0].action
    assert findings[0].age_seconds == 601


def test_the_threshold_is_strictly_greater_than() -> None:
    """边界: 正好等于阈值不算卡住(否则每次都在阈值那一秒发出噪音告警)。"""
    assert _classify([_document("edge.md", DocumentStatus.INDEXING, age_seconds=600)]) == []
    assert _classify([_document("edge.md", DocumentStatus.DELETING, age_seconds=120)]) == []


def test_a_stuck_delete_is_reported() -> None:
    findings = _classify([_document("old.md", DocumentStatus.DELETING, age_seconds=121)])
    assert [f.category for f in findings] == [CATEGORY_STUCK_DELETE]
    assert "retry the remote delete" in findings[0].action


def test_a_fresh_delete_is_not_reported() -> None:
    assert _classify([_document("going.md", DocumentStatus.DELETING, age_seconds=1)]) == []


def test_a_failed_row_needs_no_age_threshold() -> None:
    """FAILED 是终态等人处理: 它不需要"多久算卡住", 出现即待办。"""
    findings = _classify(
        [_document("bad.md", DocumentStatus.FAILED, reason="InvalidOperationError: boom")]
    )
    assert [f.category for f in findings] == [CATEGORY_FAILED]
    assert findings[0].reason == "InvalidOperationError: boom"
    assert "re-uploading" in findings[0].action
    assert findings[0].remote_outcome_unknown is False


def test_a_failed_row_without_a_verdict_tells_the_operator_to_probe_first() -> None:
    """没有定论时"重传"不是第一步 —— 远端可能已经写入了, 先探活。"""
    findings = _classify(
        [
            _document(
                "maybe.md",
                DocumentStatus.FAILED,
                reason="no verdict from OpenRAG: Timeout",
                outcome_unknown=True,
            )
        ]
    )
    assert findings[0].category == CATEGORY_FAILED
    assert "probe first" in findings[0].action
    assert findings[0].remote_outcome_unknown is True


def test_a_confirmed_tombstone_needs_nothing() -> None:
    """已确认的墓碑是"删除已完成"的记录 —— 它没有欠任何动作。"""
    assert _classify([_document("gone.md", DocumentStatus.DELETED)]) == []


def test_an_unconfirmed_tombstone_owes_a_retry() -> None:
    findings = _classify(
        [_document("gone.md", DocumentStatus.DELETED, outcome_unknown=True)]
    )
    assert [f.category for f in findings] == [CATEGORY_UNCONFIRMED_DELETE]
    assert "retry the remote delete" in findings[0].action


def test_indexed_rows_are_never_findings() -> None:
    assert _classify([_document("ok.md", DocumentStatus.INDEXED, age_seconds=99999)]) == []


def test_findings_are_ordered_oldest_first() -> None:
    findings = _classify(
        [
            _document("new.md", DocumentStatus.FAILED, age_seconds=60),
            _document("ancient.md", DocumentStatus.FAILED, age_seconds=99999),
            _document("old.md", DocumentStatus.FAILED, age_seconds=6000),
        ]
    )
    assert [f.stored_filename for f in findings] == [
        "acme/ancient.md",
        "acme/old.md",
        "acme/new.md",
    ]


# ── 远端比对: 幽灵 / 缺失 / 陌生命名空间 ────────────────────────────────────
NAMESPACES = {"acme/": "acme", "acme-eu/": "acme-eu"}


def _compare(remote: list[str], *, registered: set[str], indexed: set[str]):
    return compare_remote(
        registered=registered,
        indexed=indexed,
        remote=remote,
        namespaces=NAMESPACES,
    )


def test_a_remote_document_nobody_registered_is_a_ghost() -> None:
    comparison = _compare(["acme/ghost.md"], registered=set(), indexed=set())
    assert comparison.ghosts == ["acme/ghost.md"]
    assert comparison.missing == []


def test_a_known_row_makes_it_not_a_ghost_even_when_failed() -> None:
    """登记行是"我们知道它"的记录 —— 失败行与墓碑都算知道。"""
    comparison = _compare(
        ["acme/bad.md", "acme/gone.md"],
        registered={"acme/bad.md", "acme/gone.md"},
        indexed=set(),
    )
    assert comparison.ghosts == []


def test_an_indexed_row_whose_content_is_gone_is_missing() -> None:
    comparison = _compare([], registered={"acme/a.md"}, indexed={"acme/a.md"})
    assert comparison.missing == ["acme/a.md"]


def test_a_failed_row_is_not_reported_as_missing() -> None:
    """只有 INDEXED 承诺"远端有内容"; 失败行本来就不该在远端。"""
    comparison = _compare([], registered={"acme/bad.md"}, indexed=set())
    assert comparison.missing == []


def test_a_document_outside_every_namespace_is_reported_separately() -> None:
    """没有租户前缀的文档(命名空间改造前的遗留)任何租户都检索不到, 单独一类。"""
    comparison = _compare(["legacy.md"], registered=set(), indexed=set())
    assert comparison.unknown_namespace == ["legacy.md"]
    assert comparison.ghosts == []


def test_a_tenant_whose_slug_prefixes_another_does_not_steal_its_documents() -> None:
    """`acme-eu/` 不能被算成 `acme/` 的 —— 而这条不需要"最长前缀优先"来保证。"""
    comparison = _compare(["acme-eu/x.md"], registered=set(), indexed=set())
    assert comparison.ghosts == ["acme-eu/x.md"]
    assert comparison.unknown_namespace == []


def test_the_namespace_invariant_that_makes_prefixes_unambiguous() -> None:
    """前缀之间不可能重叠, 因为命名空间自带分隔符。

    这是上面那条测试之所以成立的原因, 也是 `_namespace_of` 不需要 tie-break 的原因:
    `"acme/"` 匹配不到 `"acme-eu/x.md"`。如果哪天有人把尾斜杠去掉, 归属判断会开始
    张冠李戴 —— 所以钉住的是这条性质, 而不是在匹配处堆防御代码。
    """
    tenant = Tenant(id=TENANT, name="Acme", slug="acme")
    assert tenant.document_namespace == "acme/"
    assert not "acme-eu/x.md".startswith(tenant.document_namespace)


def test_comparison_output_is_deterministic() -> None:
    comparison = _compare(
        ["acme/z.md", "acme/a.md", "legacy.md"],
        registered=set(),
        indexed={"acme/m2.md", "acme/m1.md"},
    )
    assert comparison.ghosts == ["acme/a.md", "acme/z.md"]
    assert comparison.missing == ["acme/m1.md", "acme/m2.md"]


# ── 渲染 ────────────────────────────────────────────────────────────────────


def _report(**kwargs) -> ReconcileReport:
    kwargs.setdefault("remote", RemoteComparison([], [], []))
    kwargs.setdefault("rules", RULES)
    kwargs.setdefault("findings", [])
    return ReconcileReport(**kwargs)


def test_the_render_shows_thresholds_counts_and_actions() -> None:
    finding = _classify([_document("slow.md", DocumentStatus.INDEXING, age_seconds=901)])[0]
    text = render_report(
        _report(
            findings=[finding],
            remote=RemoteComparison(ghosts=["acme/g.md"], missing=["acme/m.md"], unknown_namespace=["legacy.md"]),
        )
    )
    assert "indexing > 10m" in text and "deleting > 2m" in text
    assert "[stuck-upload] acme acme/slow.md" in text and "age=15.0m" in text
    assert f"next: {finding.action}" in text
    assert "ghosts (remote, unregistered): 1" in text and "acme/g.md" in text
    assert "missing (registered, not remote): 1" in text and "acme/m.md" in text
    assert "outside every tenant namespace: 1" in text
    assert "this command never writes" in text


def test_an_empty_report_says_so_instead_of_printing_silence() -> None:
    text = render_report(_report())
    assert "nothing needs attention" in text
    assert "never writes" not in text


def test_an_unreadable_remote_is_named_and_not_read_as_absence() -> None:
    text = render_report(
        _report(
            remote_errors=[RemoteUnavailable(tenant_slug="acme", detail="Timeout: nope")]
        )
    )
    assert "could not be read: 1" in text
    assert "acme: Timeout: nope" in text
    assert "an unreadable remote is not an empty one" in text


def test_needs_attention_covers_every_section() -> None:
    assert _report().needs_attention is False
    assert _report(findings=[_classify([_document("f.md", DocumentStatus.FAILED)])[0]]).needs_attention
    assert _report(remote=RemoteComparison(["acme/g.md"], [], [])).needs_attention
    assert _report(remote=RemoteComparison([], ["acme/m.md"], [])).needs_attention
    assert _report(remote=RemoteComparison([], [], ["legacy.md"])).needs_attention
    assert _report(remote_errors=[RemoteUnavailable("acme", "boom")]).needs_attention


# ── 服务接线(假网关, 真库) ─────────────────────────────────────────────────


class FakeGateway:
    """Port stand-in that records calls and can fail per API key."""

    def __init__(self) -> None:
        self.listed: list[str] = []
        self.mutating_calls: list[str] = []
        self.remote: dict[str, list[str]] = {}
        self.fail_with: Exception | None = None

    def list_document_filenames(self, *, api_key: str) -> list[str]:
        self.listed.append(api_key)
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.remote.get(api_key, []))

    def __getattr__(self, name: str):
        # Any other port method is a mutation as far as this command is
        # concerned: the report must never call one.
        def _record(**kwargs):
            self.mutating_calls.append(name)
            raise AssertionError(f"reconcile must not call {name}")

        return _record


@pytest.fixture(autouse=True)
def _pinned_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "openrag_api_key", "orag_test_key", raising=False)


@ pytest.fixture
async def registry() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            from openrag_lab.infrastructure.db.seed import seed_identity

            await seed_identity(session)
            await SqlTenantRepository(session).save(
                Tenant(id=TENANT, name="Acme", slug="acme")
            )
            await SqlUserRepository(session).save(
                User(id=UserId("u-alice"), tenant_id=TENANT, username="alice", password_hash="h")
            )
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


async def test_the_service_reports_both_sides_registry_and_remote(registry) -> None:
    async with registry() as session:
        await SqlDocumentRepository(session).save(
            _document("ok.md", DocumentStatus.INDEXED)
        )
        await SqlDocumentRepository(session).save(
            _document("bad.md", DocumentStatus.FAILED, reason="boom")
        )
        await session.commit()

    gateway = FakeGateway()
    gateway.remote["orag_test_key"] = ["acme/ok.md", "acme/ghost.md"]
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert [f.stored_filename for f in report.findings] == ["acme/bad.md"]
    assert report.remote.ghosts == ["acme/ghost.md"]
    assert report.remote.missing == []
    assert gateway.listed == ["orag_test_key"]
    assert gateway.mutating_calls == [], "对账报告阶段绝不能写任何东西"


async def test_the_service_reports_a_missing_document(registry) -> None:
    async with registry() as session:
        await SqlDocumentRepository(session).save(_document("ok.md", DocumentStatus.INDEXED))
        await session.commit()

    gateway = FakeGateway()  # 远端什么都没有
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.remote.missing == ["acme/ok.md"]
    assert report.needs_attention is True


async def test_an_unreadable_remote_does_not_become_a_sea_of_missing_documents(
    registry,
) -> None:
    """读不到 ≠ 没有: 否则一次超时会把整个租户的健康文档报成 missing。"""
    async with registry() as session:
        await SqlDocumentRepository(session).save(_document("ok.md", DocumentStatus.INDEXED))
        await session.commit()

    gateway = FakeGateway()
    gateway.fail_with = TimeoutError("OpenRAG request failed: timeout")
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.remote.missing == [], "读失败的租户必须被排除在比对之外"
    assert len(report.remote_errors) == 1
    assert report.remote_errors[0].tenant_slug == "acme"
    assert "TimeoutError" in report.remote_errors[0].detail
    assert report.needs_attention is True


async def test_the_local_half_survives_an_unreachable_remote(registry) -> None:
    """报告的一半不需要网络 —— 远端挂了正是最需要看本地那一半的时候。"""
    async with registry() as session:
        await SqlDocumentRepository(session).save(
            _document("bad.md", DocumentStatus.FAILED, reason="boom")
        )
        await session.commit()

    gateway = FakeGateway()
    gateway.fail_with = TimeoutError("timeout")
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert [f.stored_filename for f in report.findings] == ["acme/bad.md"]


async def test_a_tenant_filter_limits_the_walk(registry) -> None:
    async with registry() as session:
        await SqlDocumentRepository(session).save(
            _document("bad.md", DocumentStatus.FAILED, reason="boom")
        )
        await session.commit()

    gateway = FakeGateway()
    async with registry() as session:
        service = ReconcileService(session, gateway, rules=RULES)
        unknown = await service.run(tenant_slugs=["nope"])
        known = await service.run(tenant_slugs=["acme"])

    assert unknown.findings == []
    assert gateway.listed == ["orag_test_key"], "过滤掉的租户不该被访问"
    assert [f.stored_filename for f in known.findings] == ["acme/bad.md"]


async def test_repository_query_selects_exactly_the_unsettled_rows(registry) -> None:
    """`list_unsettled` 是报告的输入: 多一行就是噪音, 少一行就是漏报。"""
    async with registry() as session:
        repository = SqlDocumentRepository(session)
        await repository.save(_document("ok.md", DocumentStatus.INDEXED))
        await repository.save(_document("live.md", DocumentStatus.INDEXING))
        await repository.save(_document("bad.md", DocumentStatus.FAILED))
        await repository.save(_document("going.md", DocumentStatus.DELETING))
        await repository.save(_document("gone.md", DocumentStatus.DELETED))
        await repository.save(
            _document("ghost-delete.md", DocumentStatus.DELETED, outcome_unknown=True)
        )
        await session.commit()

    async with registry() as session:
        rows = await SqlDocumentRepository(session).list_unsettled(TENANT)
        all_names = await SqlDocumentRepository(session).list_all_stored_filenames(TENANT)

    assert {row.display_name for row in rows} == {
        "live.md",
        "bad.md",
        "going.md",
        "ghost-delete.md",
    }
    # 已确认的墓碑不该进待办, 但它仍然算"我们知道的名字"
    assert "gone.md" not in {row.display_name for row in rows}
    assert "acme/gone.md" in all_names
