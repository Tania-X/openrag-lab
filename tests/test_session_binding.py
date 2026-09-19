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

from pathlib import Path

import pytest

from openrag_lab.config import get_settings
from openrag_lab.infrastructure.db import session as db_session


@pytest.fixture(autouse=True)
def _unbound_process():
    """Every test here needs an unbound process, exactly like a fresh CLI run."""
    db_session.reset_db()
    yield
    db_session.reset_db()


def test_create_all_binds_the_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_all()` 必须问配置要库, 而不是回落到默认路径。

    回归自演练: 配置指向 /tmp/x.db 却绑到了 data/openrag-lab.db, 于是
    `reconcile`(只读, 读错库) 与 `reingest-legacy`(**会写**, 写错库) 都静默跑偏。
    """
    import asyncio

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
    db_session.reset_db()
    engine = db_session.init_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    assert Path(engine.url.database or "") == tmp_path / "b.db"
