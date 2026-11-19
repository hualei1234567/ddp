"""N9-008：采集会话 → RawAsset / Episode 归属链路

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-19

为什么加列而不是新建关联表
--------------------------
会话与产物的关系是 **1:N**（一次执行可以产出多个 RawAsset / Episode），
且归属是产物的**固有属性**而非多对多关系：

- 一个 RawAsset 只可能来自一次执行；一个 Episode 也只属于一次执行；
- 既有 ``raw_assets`` / ``episodes`` 已经用 ``(project_id, collection_task_id)``
  / ``(project_id, batch_id)`` 复合外键表达"同项目归属"，本列沿用同一形状；
- 独立关联表会让"某产物属于哪个会话"有两个真相来源（关联表 + 产物表），
  查询要 JOIN，写入要保证两边一致，反而制造漂移机会。

因此复用既有表，各加一列可空 ``capture_session_id``，并以
``(project_id, capture_session_id) -> capture_sessions(project_id, id)``
复合外键强制**同项目**，跨项目引用在数据库层直接拒绝。

``upload_sessions`` 同样加列：RawAsset 由上传完成时产出（没有独立的
RawAsset 创建接口），要让"创建即归属"复用既有链路，归属必须随上传会话
带下来，完成时复制到 RawAsset。这样 RawAsset 与 Episode 两条创建链路
都能在创建时确定归属，不需要第二套创建接口。

为什么可空
----------
历史产物（本迁移前创建）没有会话归属，语义正是"未记录执行来源"。设
NOT NULL 会因存量行失败；补造会话是伪造事实。新写入由应用层按需填。

为什么 ``ondelete=RESTRICT``
----------------------------
会话是执行记录，本身有禁删触发器；即使将来放开，也不允许通过删会话让
产物悬空。与既有 ``fk_raw_assets_project_task`` 等保持一致。

SQLite 与 PostgreSQL 的差异
---------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，因此复合外键只在
PostgreSQL 上通过 ``create_foreign_key`` 建立；SQLite 测试库走
``Base.metadata.create_all``，外键随 ORM 元数据一次建出（与迁移 0033
给 ``data_source_media_assets`` 加外键时的取舍相同）。两库都建索引，
保证按会话过滤产物时走索引而非全表扫描。

回滚
----
``downgrade`` 先删外键（仅 PG）、再删索引与列，可反复升降级。删列不涉及
任何其他对象；升级前后三张表的既有列与行为完全不变，未填归属的历史行
保持 ``NULL``。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

# 表名 -> 索引名；顺序稳定，便于 downgrade 逆序卸载。
_TABLES: tuple[tuple[str, str], ...] = (
    ("upload_sessions", "ix_upload_sessions_project_capture_session"),
    ("raw_assets", "ix_raw_assets_project_capture_session"),
    ("episodes", "ix_episodes_project_capture_session"),
)
_FK_NAMES: dict[str, str] = {
    "upload_sessions": "fk_upload_sessions_project_capture_session",
    "raw_assets": "fk_raw_assets_project_capture_session",
    "episodes": "fk_episodes_project_capture_session",
}


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"
    for table, index_name in _TABLES:
        op.add_column(table, sa.Column("capture_session_id", sa.Uuid(), nullable=True))
        if is_postgresql:
            op.create_foreign_key(
                _FK_NAMES[table],
                table,
                "capture_sessions",
                ["project_id", "capture_session_id"],
                ["project_id", "id"],
                ondelete="RESTRICT",
            )
        op.create_index(index_name, table, ["project_id", "capture_session_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"
    for table, index_name in reversed(_TABLES):
        op.drop_index(index_name, table_name=table)
        if is_postgresql:
            op.drop_constraint(_FK_NAMES[table], table, type_="foreignkey")
        op.drop_column(table, "capture_session_id")
