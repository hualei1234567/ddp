"""N7-002：外部训练引用的配置摘要与版本标识

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-19

为什么加这三列
--------------
训练引用此前只记录"提交给了哪个 Adapter、外部 run 是什么、状态如何"，没有
训练 run 本身的参数与版本信息，无法回答"这次训练用的什么配置、哪份代码、
哪个模型版本"。本迁移在既有 ``external_training_references`` 上补齐：

- ``config_summary``：提交时配置的**摘要**（JSON，应用层已裁剪到 8KiB 并脱敏；
  密钥、凭据、绝对路径在写入前替换为占位符）；
- ``code_version``：训练代码/镜像版本标识（≤100 字符）；
- ``model_version``：本次 run 的模型版本标识（≤100 字符）。

不新建平行 TrainingRun 表：外部平台仍是训练执行的权威系统，引用行本身就是
DDP 侧的 run 记录（见 ADR-0036 与 backlog N7-002）。

**存量为什么保持 NULL 且不回填**：迁移前的提交没有保存配置与版本标识，任何
回填都只能靠推测（从审计/Adapter 反查并"猜"当时参数），这会把推断伪装成事实。
``config_summary IS NULL`` 表示"未记录"，与"提交时没有配置"（``{}``）语义不同；
读取方不得把 NULL 折叠成空对象。Version 标识同理，未知留空，不猜。

建库路径与幂等
--------------
建表迁移 0019 直接使用运行时 ORM 元数据建表（见
``tests/migrations/test_migrations_are_immutable_snapshots.py`` 的已知基线）。
在全新库上，0019 会先按当前模型建出这三列，0063 再 ADD COLUMN 就会
``duplicate column``。因此逐列检查是否存在，已存在则跳过——与 0051/0054/0062
同一处理方式。

回滚
----
``downgrade`` 删除三列。升级后新产生的摘要与版本标识只存在于这些列；回滚会
丢弃它们（提交事实仍可从审计与外部平台追溯），升级前的引用行完全不动。
SQLite 删列会重建表并丢掉触发器，因此回滚前后显式卸载/重装训练守卫
（``drop_training_guards`` / ``install_training_guards``），避免回滚后
外部引用的状态与禁删兜底静默失效。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.training_guards import (
    drop_training_guards,
    install_training_guards,
)
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

_TABLE = "external_training_references"
# 与 ORM 模型 ``_JSON_TYPE`` 对齐：PG 用 JSONB，SQLite 用 JSON。
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_COLUMNS: dict[str, sa.types.TypeEngine] = {
    "config_summary": _JSON_TYPE,
    "code_version": sa.String(length=100),
    "model_version": sa.String(length=100),
}


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    for name, column_type in _COLUMNS.items():
        if name in existing:
            continue
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    targets = [name for name in reversed(_COLUMNS) if name in existing]
    if not targets:
        return
    # SQLite 的 ``batch_alter_table`` 删除列时会重建整张表，触发器不会随表
    # 一起重建（迁移 0041 已记录同一硬约束）。训练守卫必须显式卸载后重装，
    # 否则回滚会静默丢掉 ``external_training_references`` 的状态/禁删兜底。
    drop_training_guards(op.get_bind())
    for name in targets:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(name)
    install_training_guards(op.get_bind())
