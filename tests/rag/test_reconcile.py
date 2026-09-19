"""Reconciliation report tests (design §7, stage P3).

This stage is read-only, so most of what can go wrong is a *judgement*: which
rows deserve attention, whether a remote name is really a ghost, whether an
unreadable remote was mistaken for an empty one. Every one of those is a pure
function here, so they are tested directly — no database, no network — and the
service wiring is tested with fakes, including a guard that it never writes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from openrag_lab.application.rag.reconcile import (
    CATEGORY_FAILED,
    CATEGORY_INCONSISTENT,
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
from openrag_lab.cli import app
from openrag_lab.client import OpenRAGError
from openrag_lab.config import get_settings
from openrag_lab.domain.identity.models import Document, Tenant, User
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.errors import NotFoundError
from openrag_lab.domain.shared.ids import DocumentId, TenantId, UserId
from openrag_lab.infrastructure.db import models  # noqa: F401
from openrag_lab.infrastructure.db import session as db_session
from openrag_lab.infrastructure.db.base import Base
from openrag_lab.infrastructure.db.repositories.identity import (
    SqlDocumentRepository,
    SqlTenantRepository,
    SqlUserRepository,
)
from openrag_lab.infrastructure.db.seed import seed_identity

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
    # 一个字段一行, 文件名单独占行: 名字是用户给的, 可能长到换行, 以前会把
    # "age=… status=…" 挤到续行上糊成一团。
    assert "[stuck-upload] acme/slow.md" in text
    assert "tenant acme | age 15.0m | status indexing" in text
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
        report = await ReconcileService(session, gateway, rules=RULES).run(
            tenant_slugs=["acme"]
        )

    assert [f.stored_filename for f in report.findings] == ["acme/bad.md"]
    assert gateway.listed == ["orag_test_key"]


async def test_an_unknown_tenant_slug_is_refused_not_reported_as_healthy(
    registry,
) -> None:
    """评审第 3 轮(4 级): slug 打错不能和"一切正常"共用同一个信号。

    空报告与健康报告长得一模一样 —— "nothing needs attention" + 退出码 0 ——
    而这个命令正是给 cron 用的。所以过滤后匹配不到就是调用方错误, 直接拒绝。
    """
    gateway = FakeGateway()
    async with registry() as session:
        with pytest.raises(NotFoundError) as caught:
            await ReconcileService(session, gateway, rules=RULES).run(
                tenant_slugs=["typo"]
            )

    assert "typo" in str(caught.value)
    assert gateway.listed == [], "连远端都不该访问"


async def test_a_partially_matching_filter_is_refused_too(registry) -> None:
    """一个对、一个错也不行: 静默忽略打错的那个, 等于悄悄改变了报告的范围。"""
    gateway = FakeGateway()
    async with registry() as session:
        with pytest.raises(NotFoundError) as caught:
            await ReconcileService(session, gateway, rules=RULES).run(
                tenant_slugs=["acme", "typo"]
            )
    assert "typo" in str(caught.value)


async def test_an_empty_filter_is_refused(registry) -> None:
    """没有任何租户可报时, 拒绝比"空报告"诚实。"""
    gateway = FakeGateway()
    async with registry() as session:
        with pytest.raises(NotFoundError):
            await ReconcileService(session, gateway, rules=RULES).run(tenant_slugs=[])


async def test_no_filter_still_reports_every_tenant(registry) -> None:
    """反向守卫: 不传过滤是正常用法, 不能被上面的拒绝逻辑误伤。"""
    async with registry() as session:
        await SqlDocumentRepository(session).save(
            _document("bad.md", DocumentStatus.FAILED, reason="boom")
        )
        await session.commit()

    gateway = FakeGateway()
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert [f.stored_filename for f in report.findings] == ["acme/bad.md"]


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


# ── 配置缺失 ≠ 远端故障(评审第 1 轮 issue ①) ──────────────────────────────


async def test_a_missing_api_key_is_reported_as_configuration_not_as_a_remote_failure(
    registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve_tenant_scope` 自己说这是 deployment error, 不是租户级失败。

    混进 `remote_errors` 会让运维把"没配 key"读成"OpenRAG 挂了" —— 两者要去的地方
    完全不同(一个改本部署的配置, 一个去 OpenRAG 那边查)。
    """
    monkeypatch.setattr(get_settings(), "openrag_api_key", "", raising=False)
    async with registry() as session:
        await SqlDocumentRepository(session).save(
            _document("bad.md", DocumentStatus.FAILED, reason="boom")
        )
        await session.commit()

    gateway = FakeGateway()
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.configuration_problem == "OPENRAG_API_KEY is not configured"
    assert report.remote_errors == []
    assert gateway.listed == [], "key 都没有, 不该发出任何远端调用"
    assert [f.stored_filename for f in report.findings] == ["acme/bad.md"], "本地那一半照常"
    assert report.needs_attention is True

    text = render_report(report)
    assert "configuration problem, not a remote failure" in text
    assert "the local half below is unaffected" in text
    assert "could not be read: 1" not in text, "不能同时报成租户级远端失败"


async def test_a_configuration_problem_stops_the_walk_instead_of_repeating_itself(
    registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """所有租户都会以同样方式失败 —— 报一次就停, 不要每个租户刷一行。"""
    monkeypatch.setattr(get_settings(), "openrag_api_key", "", raising=False)
    async with registry() as session:
        await SqlTenantRepository(session).save(
            Tenant(id=TenantId("t-globex"), name="Globex", slug="globex")
        )
        await session.commit()

    gateway = FakeGateway()
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.configuration_problem is not None
    assert gateway.listed == []


async def test_a_real_remote_failure_is_still_a_tenant_level_error(registry) -> None:
    """反向守卫: 真·远端故障必须留在 remote_errors 里, 不能被提升成配置问题。"""
    async with registry() as session:
        await SqlDocumentRepository(session).save(_document("ok.md", DocumentStatus.INDEXED))
        await session.commit()

    gateway = FakeGateway()
    gateway.fail_with = TimeoutError("timeout")
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.configuration_problem is None
    assert [e.tenant_slug for e in report.remote_errors] == ["acme"]


# ── 候选超集 vs 分类: 不许有"取出来又静默丢掉"的格子(评审第 2 轮) ──────────


def test_a_flag_that_contradicts_the_status_is_reported_not_swallowed() -> None:
    """`INDEXED` + 没有定论 = 自相矛盾: 它声称有定论, 标记却说从没拿到定论。

    当前写入路径到不了这里(`mark_indexed` 会清标记, 有测试钉着), 但"到不了"是
    关于**现在**的断言。一旦哪天有写路径把它留下来, 报告必须自己喊出来 —— 报告存在的
    意义就是"两边对不上就说出来", 而不是替坏掉的写路径打掩护。
    """
    findings = _classify([_document("weird.md", DocumentStatus.INDEXED, outcome_unknown=True)])
    assert [f.category for f in findings] == [CATEGORY_INCONSISTENT]
    assert "write path" in findings[0].action


def test_the_contradiction_outranks_the_age_gate() -> None:
    """否则"可能还在途"会变成矛盾行的藏身处: 过渡态+标记, 且年龄没超阈值。"""
    fresh = _classify(
        [_document("live.md", DocumentStatus.INDEXING, age_seconds=1, outcome_unknown=True)]
    )
    assert [f.category for f in fresh] == [CATEGORY_INCONSISTENT]

    fresh_delete = _classify(
        [_document("going.md", DocumentStatus.DELETING, age_seconds=1, outcome_unknown=True)]
    )
    assert [f.category for f in fresh_delete] == [CATEGORY_INCONSISTENT]


def test_every_status_and_flag_combination_has_a_defined_verdict() -> None:
    """把整个状态空间走一遍, 钉住"哪些格子会变成 finding"。

    这条测试是查询与分类之间的耦合守卫: 查询是候选**超集**, 分类是判定。唯一允许被
    丢掉的组合是"还在合法时限内的过渡态"(新鲜的 INDEXING/DELETING)—— 它们本来就是
    候选而不是待办。其余每一格都必须有明确结论。
    """
    expected: dict[tuple[DocumentStatus, bool], str | None] = {
        (DocumentStatus.INDEXED, False): None,
        (DocumentStatus.INDEXED, True): CATEGORY_INCONSISTENT,
        (DocumentStatus.INDEXING, False): None,          # 新鲜: 合法在途
        (DocumentStatus.INDEXING, True): CATEGORY_INCONSISTENT,
        (DocumentStatus.FAILED, False): CATEGORY_FAILED,
        (DocumentStatus.FAILED, True): CATEGORY_FAILED,
        (DocumentStatus.DELETING, False): None,          # 新鲜: 合法在途
        (DocumentStatus.DELETING, True): CATEGORY_INCONSISTENT,
        (DocumentStatus.DELETED, False): None,           # 已确认的墓碑不欠动作
        (DocumentStatus.DELETED, True): CATEGORY_UNCONFIRMED_DELETE,
    }
    for (status, unknown), want in expected.items():
        rows = [_document("x.md", status, outcome_unknown=unknown)]
        got = [f.category for f in _classify(rows)]
        assert got == ([want] if want else []), f"{status.value}/unknown={unknown} -> {got}"

    # 过渡态超龄后必须从"候选"升级为"待办", 且分类不因标记以外的因素改变
    aged = {
        DocumentStatus.INDEXING: CATEGORY_STUCK_UPLOAD,
        DocumentStatus.DELETING: CATEGORY_STUCK_DELETE,
    }
    for status, want in aged.items():
        rows = [_document("x.md", status, age_seconds=100000)]
        assert [f.category for f in _classify(rows)] == [want]


# ── CLI 层的信号: 退出码就是用户可见的结论(评审第 3 轮) ────────────────────


@pytest.fixture
def cli_database(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI at a temp database, in a process that has no engine yet.

    ``reset_db()`` is not optional: ``init_db`` now *refuses* to switch databases
    inside one process (that refusal is the fix for a command silently reading
    ``data/openrag-lab.db`` no matter what ``DATABASE_URL`` said), so each test
    that drives the CLI has to start from an unbound process — exactly like a
    real invocation does.
    """
    monkeypatch.setattr(
        get_settings(), "database_url", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    )
    asyncio.run(db_session.reset_db())
    try:
        yield tmp_path / "cli.db"
    finally:
        asyncio.run(db_session.reset_db())


def test_the_cli_exits_non_zero_for_an_unknown_tenant(cli_database) -> None:
    """这个 bug 的用户可见形态就在退出码上 —— 所以在这里再钉一次。

    真实调用(临时库、真 CLI 入口、不碰网络): `--tenant typo` 必须非零退出,
    且**不能**出现"nothing needs attention"。前者是 cron 唯一能看到的信号, 后者是
    它最不能看到的一句话。
    """
    result = CliRunner().invoke(app, ["reconcile", "--tenant", "typo"])

    assert result.exit_code == 1, result.output
    assert "typo" in result.output
    assert "nothing needs attention" not in result.output
    # 退出码还不够: 一个未捕获的 NameError 也会让 CliRunner 返回 1。必须钉住
    # "是**有意的** typer.Exit", 否则这条测试会在代码崩掉时照样变绿。
    assert isinstance(result.exception, SystemExit), (
        f"应是有意的 typer.Exit, 实际 {type(result.exception).__name__}: {result.exception}"
    )
    assert "Traceback" not in result.output


async def test_a_truncated_remote_listing_becomes_unreadable_not_a_screen_of_missing(
    registry,
) -> None:
    """评审第 5 轮(4 级): "读到了"不等于"读全了"。

    网关在名单可能被截断时抛错(见 `list_document_filenames` 的契约), 服务层照旧把它
    当成"这个租户读不到" —— 于是报告如实说读不到, 而不是把被截掉的名字全报成 missing。
    """
    async with registry() as session:
        for index in range(3):
            await SqlDocumentRepository(session).save(
                _document(f"doc{index}.md", DocumentStatus.INDEXED)
            )
        await session.commit()

    gateway = FakeGateway()
    gateway.fail_with = OpenRAGError("listing hit its 500-entry ceiling")
    async with registry() as session:
        report = await ReconcileService(session, gateway, rules=RULES).run()

    assert report.remote.missing == [], "截断绝不能变成 missing"
    assert [e.tenant_slug for e in report.remote_errors] == ["acme"]
    assert "ceiling" in report.remote_errors[0].detail
    assert report.needs_attention is True


def test_the_cli_refuses_an_explicitly_empty_tenant(cli_database) -> None:
    """`--tenant ""` 必须与 `--tenant typo` 一样被拒绝, 不能被悄悄放宽成全量。

    修复前: 空字符串 falsy → CLI 把过滤构造成 None → 走"遍历全部租户"分支 ——
    服务层那条空过滤守卫(有测试)在 CLI 路径上**根本不可达**。这正是"只测了渲染/
    服务层, 没测接线"的经典空转: `test_an_empty_filter_is_refused` 直接调服务层,
    碰不到 CLI 的短路。
    """
    result = CliRunner().invoke(app, ["reconcile", "--tenant", ""])

    assert result.exit_code == 1, result.output
    assert "nothing needs attention" not in result.output, "空值不能变成一次全量绿灯"
    assert isinstance(result.exception, SystemExit), (
        f"应是有意的 typer.Exit, 实际 {type(result.exception).__name__}"
    )
    assert "Traceback" not in result.output


# ── 脏库演练: 操作员**看到的那一份**(演练的产物) ─────────────────────────
# 这一节存在的理由: 上面的单测断言的是 `render_report()` 返回的字符串, 而操作员看到的
# 是 CLI 打印出来的那一份 —— 两者并不相同。第一次人眼演练就发现 Rich 把 `[category]`
# 当样式标签吃掉了: 单测全绿, 而真实输出里**一个分类标签都没有**。


class _DrillGateway:
    """Stands in for the gateway: one ghost, no network, no mutations."""

    def __init__(self, *, remote: list[str] | None = None, fail: bool = False) -> None:
        self._remote = remote or []
        self._fail = fail
        self.closed = False

    def list_document_filenames(self, *, api_key: str) -> list[str]:
        if self._fail:
            raise OpenRAGError("OpenRAG listing returned 500 entries, at its 500-entry ceiling")
        return list(self._remote)

    def close(self) -> None:
        self.closed = True


def _seed_drill_rows(factory) -> None:
    """Every category, plus the three controls that must stay silent."""
    from openrag_lab.domain.identity.models import Document, Tenant, User
    from openrag_lab.infrastructure.db.repositories.identity import (
        SqlDocumentRepository,
        SqlTenantRepository,
        SqlUserRepository,
    )

    async def seed() -> None:
        async with factory() as session:
            await seed_identity(session)
            await SqlTenantRepository(session).save(
                Tenant(id=TenantId("t-acme"), name="Acme", slug="acme")
            )
            await SqlUserRepository(session).save(
                User(id=UserId("u-alice"), tenant_id=TenantId("t-acme"), username="alice", password_hash="h")
            )
            repository = SqlDocumentRepository(session)
            rows = [
                # 出现的
                ("stuck-upload.md", DocumentStatus.INDEXING, 3 * 3600, None, False),
                ("stuck-delete.md", DocumentStatus.DELETING, 900, None, False),
                ("failed.md", DocumentStatus.FAILED, 7200, "boom", False),
                ("failed-unknown.md", DocumentStatus.FAILED, 1800, "timeout", True),
                ("tombstone-unknown.md", DocumentStatus.DELETED, 400, "timeout", True),
                ("contradiction.md", DocumentStatus.INDEXED, 60, "stale flag", True),
                # 一条以"天"为单位的行: 年龄格式化必须真的进位(演练发现
                # 400 天的行被印成 "9600.0h")
                ("ancient.md", DocumentStatus.FAILED, 400 * 86400, "boom", False),
                # 对照组: 必须沉默
                ("in-flight.md", DocumentStatus.INDEXING, 30, None, False),
                ("tombstone-ok.md", DocumentStatus.DELETED, 400, "removed 7 chunk(s)", False),
                ("healthy.md", DocumentStatus.INDEXED, 60, None, False),
            ]
            for index, (name, status, age, reason, unknown) in enumerate(rows):
                document = Document(
                    id=DocumentId(f"d{index}"), tenant_id=TenantId("t-acme"),
                    stored_filename=f"acme/{name}", display_name=name,
                    uploaded_by=UserId("u-alice"), status=status,
                    status_reason=reason, remote_outcome_unknown=unknown,
                )
                document.updated_at = datetime.now(UTC) - timedelta(seconds=age)
                await repository.save(document)
            await session.commit()

    asyncio.run(seed())


def _run_cli(monkeypatch, cli_database, gateway: _DrillGateway, *args: str):
    """Invoke the CLI with the gateway replaced and the schema seeded.

    `reset_db` + an explicit `create_all()` first: the engine is bound strictly
    now, so the test has to set the database up the same way a real run does.
    """
    import asyncio

    from openrag_lab.infrastructure.openrag import openrag_port_impl

    asyncio.run(db_session.reset_db())
    asyncio.run(db_session.create_all())
    factory = async_sessionmaker(db_session._engine, expire_on_commit=False)
    _seed_drill_rows(factory)

    # The CLI imports OpenRAGGateway inside `_reconcile`, so patching the module
    # attribute is the seam that works without a live OpenRAG.
    monkeypatch.setattr(openrag_port_impl, "OpenRAGGateway", lambda **_: gateway)
    return CliRunner().invoke(app, ["reconcile", *args])


def test_the_operator_facing_report_shows_every_category(cli_database, monkeypatch) -> None:
    """演练的核心断言: **操作员看到的**那份里, 六个分类标签都在。

    Rich 会把 `[failed]` 当样式标签吃掉 —— 单测断言 `render_report()` 的返回值,
    永远发现不了这件事。所以这里断言 CLI 的实际输出。
    """
    result = _run_cli(
        monkeypatch,
        cli_database,
        # 远端要包含健康行: 否则它会(正确地)被报成 missing, 对照组就失去意义了。
        _DrillGateway(remote=["acme/ghost.md", "acme/healthy.md"]),
    )

    assert result.exit_code == 0, result.output
    for label in (
        "[stuck-upload]",
        "[stuck-delete]",
        "[failed]",
        "[unconfirmed-delete]",
        "[inconsistent]",
    ):
        assert label in result.output, f"分类标签 {label} 没出现在操作员看到的输出里"
    # 对照组必须沉默: 在途上传 / 已确认墓碑 / 健康行
    assert "in-flight.md" not in result.output
    assert "tombstone-ok.md" not in result.output
    assert "healthy.md" not in result.output
    # 长度可读: 年龄用天/小时/分钟, 不是"9600.0h"
    assert "age 3.0h" in result.output
    assert "age 400.0d" in result.output, "以天为单位的老行不能被印成 9600.0h"
    assert "tenant acme | age" in result.output
    # 远端那一半: 幽灵报出来, 且它属于 acme 命名空间
    assert "ghosts (remote, unregistered): 1" in result.output
    assert "acme/ghost.md" in result.output
    assert "unsettled rows: 7" in result.output


def test_the_operator_facing_report_is_strict_about_a_dirty_registry(
    cli_database, monkeypatch
) -> None:
    """脏库 + --strict → 退出码 1(这是 cron 唯一能看到的信号)。"""
    result = _run_cli(monkeypatch, cli_database, _DrillGateway(), "--strict")
    assert result.exit_code == 1, result.output
    assert "nothing needs attention" not in result.output


def test_a_clean_registry_reports_clean_and_exits_zero(cli_database, monkeypatch) -> None:
    """反向守卫: 干净库必须真的安静(否则告警会被无视)。"""
    from openrag_lab.infrastructure.openrag import openrag_port_impl

    asyncio.run(db_session.reset_db())
    asyncio.run(db_session.create_all())
    factory = async_sessionmaker(db_session._engine, expire_on_commit=False)

    async def seed_tenant() -> None:
        async with factory() as session:
            await seed_identity(session)
            await SqlTenantRepository(session).save(
                Tenant(id=TenantId("t-acme"), name="Acme", slug="acme")
            )
            await SqlUserRepository(session).save(
                User(id=UserId("u-alice"), tenant_id=TenantId("t-acme"), username="alice", password_hash="h")
            )
            await session.commit()

    asyncio.run(seed_tenant())
    monkeypatch.setattr(openrag_port_impl, "OpenRAGGateway", lambda **_: _DrillGateway())
    result = CliRunner().invoke(app, ["reconcile", "--strict"])

    assert result.exit_code == 0, result.output
    assert "nothing needs attention" in result.output


def _flat(text: str) -> str:
    """Collapse whitespace: Rich wraps at the terminal width and splits words.

    Asserting a long phrase against the raw output is a coin flip — the real run
    printed "an unreadable \nremote is not an empty one". Normalise first.
    """
    return " ".join(text.split())


def test_the_operator_facing_report_names_the_unreadable_remote(
    cli_database, monkeypatch
) -> None:
    """远端读不到时: 本地那一半照常, 且明确写"读不到不等于没有"。"""
    result = _run_cli(monkeypatch, cli_database, _DrillGateway(fail=True))
    flat = _flat(result.output)

    assert "[stuck-upload]" in flat, "本地那一半不能因为远端挂了就消失"
    assert "could not be read: 1" in flat
    assert "an unreadable remote is not an empty one" in flat
    assert "ceiling" in flat, "失败原因要原样带出来(这里含上限提示)"
