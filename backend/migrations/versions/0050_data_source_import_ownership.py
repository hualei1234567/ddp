"""数据源导入增加采集任务归属

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-17

为什么加这两列：导入产出的 RawAsset / Episode 需要能回答「这条数据是为哪个
采集任务采的」。此前只有上传链路能挂任务，数据源导入链路两端都没有字段，
导入出来的资产 `collection_task_id` 永远是 NULL，按任务统计永远查不到。

可空 + `ON DELETE SET NULL`：历史导入没有归属是合法状态；任务/批次即使被
清理也不该连带删除导入记录（导入记录是失败也要留痕的审计事实）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("data_source_imports") as batch:
        batch.add_column(sa.Column("collection_task_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("batch_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_data_source_imports_collection_task",
            "collection_tasks",
            ["collection_task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_data_source_imports_collection_batch",
            "collection_batches",
            ["batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_data_source_imports_task",
            ["project_id", "collection_task_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("data_source_imports") as batch:
        batch.drop_index("ix_data_source_imports_task")
        batch.drop_constraint("fk_data_source_imports_collection_batch", type_="foreignkey")
        batch.drop_constraint("fk_data_source_imports_collection_task", type_="foreignkey")
        batch.drop_column("batch_id")
        batch.drop_column("collection_task_id")
