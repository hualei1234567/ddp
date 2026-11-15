"""calibration_profiles.version -> profile_version

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-15

ADR-0032 / G-03：``calibration_profiles`` 的业务版本列改名。

改名理由不是"好看"，而是**同名不同义**：``calibration_records.version`` 是乐观锁，
Profile 的 ``version`` 是业务版本号，二者在仓储与应用层紧挨着出现
（``profile.version`` / ``record.version``），读代码时极易张冠李戴。
对外 API 从 G-01 起已经叫 ``profile_version``，本迁移把 Domain / ORM / DB 拉齐。

约束与索引的处理（已实测，不靠推测）
------------------------------------
PostgreSQL 与 SQLite ≥ 3.25 的 ``ALTER TABLE ... RENAME COLUMN`` 都会**自动**
重写 CHECK 与索引里的列引用。实测重命名后：

- ``CHECK (profile_version >= 1)`` 仍然生效；
- ``UNIQUE (project_id, name, target_id, profile_version)`` 仍然生效

因此**不需要重建**唯一索引与 CHECK，也就不存在"重建期间约束缺位"的窗口。
本迁移额外做的只有一件事：把 CHECK 的**名字**从
``ck_calibration_profiles_version_positive`` 改成 ``..._profile_version_positive``，
避免约束名与新列名对不上（名字也是文档）。

为什么不用 ``batch_alter_table``
--------------------------------
batch 在 SQLite 上是 copy-and-move（临时改名表 → 建新表 → 拷贝 → 删旧表），
会连带丢掉 ``calibration_profiles`` 上的两个不可变/禁删触发器（迁移 0029），
并且要在 robots / devices / sensors / calibration_records 四张表的复合外键
注视下改表名。原生改名不触碰触发器与其它表，风险显著更低。
当前解释器（CPython 3.11 + SQLite 3.53）支持原生改名，故不启用 batch 兜底。

SQLite 的已知差异（不掩盖）
---------------------------
SQLite 没有"重命名约束"的 DDL，所以**迁移过的 SQLite 库**里 CHECK 仍叫
``ck_calibration_profiles_version_positive``（表达式已指向 ``profile_version``，
语义完全正确，只是名字旧）；而 ``Base.metadata.create_all`` 建出的库叫新名。
仅名字不同、行为一致，且 SQLite 只用于测试，不作为 PostgreSQL 的验收替代。

回滚
----
``downgrade`` 是 upgrade 的严格逆操作，可反复升级/降级。
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_TABLE = "calibration_profiles"
_OLD_COLUMN = "version"
_NEW_COLUMN = "profile_version"
_OLD_CHECK = "ck_calibration_profiles_version_positive"
_NEW_CHECK = "ck_calibration_profiles_profile_version_positive"


def _rename_check_constraint(old_name: str, new_name: str) -> None:
    """约束改名仅在 PostgreSQL 可用；SQLite 无对应语法（见模块 docstring）。"""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} RENAME CONSTRAINT {old_name} TO {new_name}")


def upgrade() -> None:
    # 先改约束名再改列名：两条语句互不依赖，先做名字可让失败点更靠前。
    _rename_check_constraint(_OLD_CHECK, _NEW_CHECK)
    op.alter_column(_TABLE, _OLD_COLUMN, new_column_name=_NEW_COLUMN)


def downgrade() -> None:
    op.alter_column(_TABLE, _NEW_COLUMN, new_column_name=_OLD_COLUMN)
    _rename_check_constraint(_NEW_CHECK, _OLD_CHECK)
