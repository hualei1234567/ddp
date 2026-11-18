"""N9-003：采集方案绑定目标 Episode 数与质量阈值

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-19

为什么加这两列
--------------
``collection_plans`` 是项目内可复用的采集定义，此前只有描述性字段，无法表达
"这份方案计划采多少条、按什么质量目标采"。本迁移在既有表上补齐：

- ``target_episode_count``：计划采集的 Episode 数。**可空**——``NULL`` 表示
  "未设定目标"，与 ``0``（明确目标为零）语义不同，故不回填、也不设默认值；
- ``quality_thresholds``：计划质量阈值（JSON）。键只允许复用数据集需求匹配
  登记的阈值键（见 ``domain/datasets/requirement_match.py``），值必须是非负
  有限数；领域层与 API 层共同校验，数据库只保证列类型与 JSON 可解析性。

**语义边界**：两列都是**计划值**，不是执行保证，也不构成自动验收依据。
实际是否达标由质量运行与人工审核结论判定；本迁移不新增任何自动放行逻辑。

为什么只 ADD COLUMN
-------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，而 ``batch_alter_table`` 在
SQLite 上会重建整张表。``collection_plans`` 被 ``collection_tasks`` 的复合外键
引用，且带有素材状态守卫触发器，重建会同时牵动子表外键与触发器。本次两列都是
"加列"且不需要新约束，因此直接 ``ADD COLUMN``，不重建表：

- ``target_episode_count`` 可空，SQLite 原生支持；
- ``quality_thresholds`` 使用常量默认值 ``'{}'``，存量行升级后即为"未指定"，
  SQLite 与 PostgreSQL 都支持带常量默认值的 ``ADD COLUMN``。

非负范围不落数据库 CHECK：SQLite 加 CHECK 必须重建表，与上面的约束冲突；
范围校验在领域层（``CollectionPlan.__post_init__``）与 API DTO 唯一实现。

回滚
----
``downgrade`` 逆序删除两列。SQLite 使用原生 ``DROP COLUMN``（3.35+）而不是
batch 重建：本表被任务外键引用且带触发器，重建会丢失触发器并在有子行时因
外键约束失败。原生删列不重建表，触发器与子表外键保持不变。回滚会丢弃升级后
写入的目标与阈值（计划本身仍可从审计追溯），升级前的行完全不动。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

_TABLE = "collection_plans"
# 与 ORM 模型 ``_JSON_TYPE`` 对齐：PG 用 JSONB，SQLite 用 JSON。
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
# 升级顺序；downgrade 逆序使用。
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, bool, str | None], ...] = (
    ("target_episode_count", sa.Integer(), True, None),
    ("quality_thresholds", _JSON_TYPE, False, "'{}'"),
)


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    for name, column_type, nullable, server_default in _COLUMNS:
        if name in existing:
            continue
        op.add_column(
            _TABLE,
            sa.Column(
                name,
                column_type,
                nullable=nullable,
                server_default=None if server_default is None else sa.text(server_default),
            ),
        )


def downgrade() -> None:
    existing = _existing_columns()
    for name, _column_type, _nullable, _server_default in reversed(_COLUMNS):
        if name not in existing:
            continue
        op.drop_column(_TABLE, name)
