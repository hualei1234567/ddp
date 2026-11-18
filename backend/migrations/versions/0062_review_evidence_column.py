"""N2-009：``data_source_reviews.evidence`` 独立 JSON 列

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-19

为什么加这一列
--------------
此前审核证据只写在 ``audit_logs.details``，读取时由应用层回填。这把
"当时凭什么通过"的核心证据绑在审计保留策略上：审计一旦归档/清理，
审核结论就失去依据；领域读取也无谓依赖审计表。本迁移给审核表增加
``evidence`` JSON 列，证据与审核结论同表、同生命周期。

**可空、不回填存量**：迁移前的审核记录没有独立证据列，它们的证据仍只
存在于审计留痕。回填需要按 ``review_id`` 反查审计、逐行解析并猜测
"最近一条 decide 记录"，既可能把历史推断伪装成事实，也会让升级耗时随
审计表线性增长。因此存量保持 NULL，读取侧按"列优先、空列回退审计"
兼容历史，行为不变（见 ``application/datasources/reviews.py``）。

为什么是 JSON 列而不是独立表
----------------------------
证据随审核整体读取，没有按字段跨审核检索的既定需求；拆表只多一次 JOIN
与一套孤儿行约束。单条证据在领域层已有白名单、键数（4）与 2000 字节
上限，不存在无限膨胀。

建库路径与幂等
--------------
建表迁移 0026 已在本任务中改写为显式 ``op.create_table``（不再引用活
ORM 模型），因此全新库升级到 0026 时**不带**本列，0062 统一 ADD COLUMN；
存量库同样没有本列。这里仍先检查列是否存在，避免历史漂移库重复加列。

回滚
----
``downgrade`` 删除本列。升级后新产生的证据只在列里安全保存，回滚会丢弃
这些证据（审计详情的副本不受影响）；升级前的数据（审核结论、原因、
审计留痕）完全不动。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

_TABLE = "data_source_reviews"
_COLUMN = "evidence"

# 与 ORM 模型 ``_REVIEW_EVIDENCE_JSON`` 对齐：PG 用 JSONB，SQLite 用 JSON。
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _has_column():
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, _JSON_TYPE, nullable=True))


def downgrade() -> None:
    if not _has_column():
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
