"""空库建表必须一次成功，且 CHECK 约束不得引用不存在的列。

为什么需要这条测试：CI 的契约快照 job 用 ``create_all`` 在**内存 SQLite** 上
建全套表。提交 e36a66f 把乐观锁列 ``version`` 从 ``ProjectScopedMixin`` 拆成
独立的 ``LockVersionMixin``，但只给部分表混入了它——``data_sources`` 等表
保留了 ``CHECK (version >= 1)`` 却没有该列，建表直接 ``no such column: version``。
当时本地有已迁移的库，跑不出来；CI 每次全新，才暴露。

这类"约束引用幽灵列"的缺陷不会在任何单表测试里出现，只有整体建表才会。
"""

from __future__ import annotations

import importlib
import pkgutil
import re

import pytest
import sqlalchemy as sa
from app.infrastructure.db import models as models_pkg
from app.infrastructure.db.base import Base
from sqlalchemy import create_engine

# 独立的 `version` 标识符：不匹配 profile_version / lock_version / schema_version。
_VERSION_IDENT = re.compile(r"(?<![A-Za-z0-9_])version(?![A-Za-z0-9_])")


def _load_all_models() -> None:
    for module in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"app.infrastructure.db.models.{module.name}")


def test_schema_builds_on_empty_database() -> None:
    """74 张表（含全部 CHECK / 索引 / 外键）必须能在空库上一把建成。"""
    _load_all_models()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    assert len(Base.metadata.tables) > 0


@pytest.mark.parametrize(
    "table_name",
    sorted(Base.metadata.tables) if Base.metadata.tables else ["<未加载>"],
)
def test_check_constraint_columns_exist(table_name: str) -> None:
    """CHECK 里引用的 ``version`` 列必须真实存在于该表。

    只检查 ``version``：其它列名要么是枚举字面量、要么已在建表测试中暴露，
    而 ``version`` 是唯一被跨 Mixin 拆合过的列，正是回归高发点。
    """
    if table_name == "<未加载>":
        _load_all_models()
    table = Base.metadata.tables[table_name]
    for constraint in table.constraints:
        if not isinstance(constraint, sa.CheckConstraint):
            continue
        sql = str(constraint.sqltext)
        if _VERSION_IDENT.search(sql):
            assert (
                "version" in table.columns
            ), f"{table_name} 的 CHECK `{sql}` 引用了不存在的 version 列"
