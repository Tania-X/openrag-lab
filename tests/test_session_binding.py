"""One process, one database — and it must be the configured one.

The drill that produced these tests was supposed to eyeball the reconciliation
report's output; its first finding was that the report was reading a *different
database* than the one it was pointed at. ``create_all()`` called ``init_db()``
with no URL, which bound the process to ``data/openrag-lab.db``; the later
``init_db(settings.database_url)`` inside ``get_session()`` was a silent no-op
because the engine was already cached.

That is the expensive kind of bug: not a crash, a *different answer*. Two things
are pinned here — the configured database is the one that gets bound, and asking
for a second, different database inside one process is refused instead of
ignored.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from openrag_lab.config import get_settings
from openrag_lab.infrastructure.db import session as db_session


@pytest.fixture(autouse=True)
def _unbound_process():
    """Every test here needs an unbound process, exactly like a fresh CLI run."""
    asyncio.run(db_session.reset_db())
    yield
    asyncio.run(db_session.reset_db())


def test_create_all_binds_the_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_all()` 必须问配置要库, 而不是回落到默认路径。

    回归自演练: 配置指向 /tmp/x.db 却绑到了 data/openrag-lab.db, 于是
    `reconcile`(只读, 读错库) 与 `reingest-legacy`(**会写**, 写错库) 都静默跑偏。
    """
    configured = tmp_path / "configured.db"
    monkeypatch.setattr(
        get_settings(), "database_url", f"sqlite+aiosqlite:///{configured}"
    )

    asyncio.run(db_session.create_all())

    assert db_session._engine is not None
    assert Path(db_session._engine.url.database or "") == configured
    assert configured.exists(), "库文件应该建在配置指向的位置"


def test_asking_for_a_second_database_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一进程里换库必须报错, 不能静默沿用第一个。

    "静默沿用"正是上一个 bug 的机制: 调用方以为自己在操作 A 库, 实际在写 B 库。
    报错至少是一次 bug report; 不报错就是一次事故。
    """
    db_session.init_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")

    with pytest.raises(RuntimeError) as caught:
        db_session.init_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")

    assert "different database" in str(caught.value)
    assert "reset_db" in str(caught.value), "报错要给出出路"


def test_the_same_database_is_not_an_error(tmp_path: Path) -> None:
    """反向守卫: 重复请求同一个库是正常用法(get_session 每次请求都会调一次)。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'same.db'}"
    first = db_session.init_db(url)
    assert db_session.init_db(url) is first


def test_reset_db_lets_a_process_rebind(tmp_path: Path) -> None:
    """出路必须真的可用(测试靠它模拟新进程)。"""
    db_session.init_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    asyncio.run(db_session.reset_db())
    engine = db_session.init_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    assert Path(engine.url.database or "") == tmp_path / "b.db"


def test_a_different_password_is_a_different_database() -> None:
    """只差密码也算换库 —— 而 `str(URL)` 会把密码渲染成 `***`, 于是守卫会放行。

    这正是本次要消灭的那类问题的一个残留分支: 凭据轮换后配置没同步时, 进程会继续用
    旧凭据的引擎而不吭声。

    直接测比较函数, 不建引擎: 密码只在 Postgres 这类 URL 上有意义, 而为了这条测试去装
    一个驱动不值得 —— 解析 URL 本身不需要它。
    """
    from sqlalchemy.engine import make_url

    from openrag_lab.infrastructure.db.session import _same_database

    rotated = make_url("postgresql+asyncpg://u:secret2@h:5432/db")
    previous = make_url("postgresql+asyncpg://u:secret1@h:5432/db")
    assert str(previous) == str(rotated), "前提: 字符串比较看不出差别(密码被掩码)"

    assert _same_database(previous, previous) is True
    assert _same_database(previous, rotated) is False, "密码不同不是同一个库"


def test_the_guard_uses_that_comparison(tmp_path: Path) -> None:
    """接线: 守卫必须走 `_same_database`, 而不是又退回字符串比较。

    这是一条**实现形状**断言, 故意写得很窄, 因为走行为的替代方案需要装一个 Postgres
    驱动(密码只在那种 URL 上有意义)才建得起引擎 —— 为一条接线测试装驱动不值得。
    只钉"用了那个函数", 不钉"init_db 里不许出现 str("(后者会误伤将来的正常用法)。
    """
    import inspect

    from openrag_lab.infrastructure.db import session as module

    assert "_same_database(" in inspect.getsource(module.init_db)


def test_reset_db_actually_disposes_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解绑必须同时断开连接, 不能只是丢掉引用。

    丢引用会让 aiosqlite 的连接在事件循环结束后被 GC, 表现为 pytest 的
    "unraisable exception" 告警 —— 而告警一多, 真问题就淹了(这条测试正是它逼出来的)。

    `AsyncEngine.dispose` 是只读属性, 打不了桩, 所以让工厂返回一个记录用的替身:
    断言的是**行为**(dispose 被调用), 不是实现细节。
    """
    from openrag_lab.infrastructure.db import session as module

    class RecordingEngine:
        def __init__(self, inner) -> None:
            self._inner = inner
            self.disposed = 0

        def __getattr__(self, name):          # 其余属性照旧透传
            return getattr(self._inner, name)

        async def dispose(self, close: bool = True) -> None:
            self.disposed += 1
            await self._inner.dispose(close)

    recorder = RecordingEngine(
        create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    )
    monkeypatch.setattr(module, "create_async_engine", lambda url, **_: recorder)

    module.init_db(f"sqlite+aiosqlite:///{tmp_path / 'dispose.db'}")
    asyncio.run(module.reset_db())

    assert recorder.disposed == 1, "reset_db 必须 dispose 引擎"
    assert module._engine is None
