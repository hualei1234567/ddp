"""外部训练引用保存回调产物元数据

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-17

为什么加这一列：Adapter 训练成功后在回调 detail 里带回产物的 object_key /
sha256 / size_bytes，但引用表此前只保留 ``model_ref``（适配器自己的逻辑引用），
产物内容只参与去重哈希、随后丢弃。结果是「训练完成 → 登记模型版本」这一步在
平台内拿不到产物地址，只能由人手工把 object_key 输进去，全链路断开。

列可空：历史引用没有回调产物是合法状态，前端必须把它渲染成"无产物信息"，
而不是当成空产物。只存元数据（≤32KB，领域层校验），不存权重本体。

**为什么这里要判列是否存在**：建表迁移 0019 直接用运行时 ORM 元数据
（``ExternalTrainingReferenceModel.__table__``）建表，而不是结构快照。
于是 ORM 加了新列后，从空库升级时 0019 建出的表**已经带 artifacts**，
本迁移再 ADD COLUMN 就会 ``duplicate column``（SQLite/PostgreSQL 都报）。
先把 0019 快照化才是根治，但那会改动历史建表路径，留作技术债；
这里用存在性判断让升级在新旧两种路径下都成立。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _has_artifacts() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "artifacts" in {
        column["name"] for column in inspector.get_columns("external_training_references")
    }


def upgrade() -> None:
    if _has_artifacts():
        return
    with op.batch_alter_table("external_training_references") as batch:
        batch.add_column(sa.Column("artifacts", _JSON, nullable=True))


def downgrade() -> None:
    if not _has_artifacts():
        return
    with op.batch_alter_table("external_training_references") as batch:
        batch.drop_column("artifacts")
