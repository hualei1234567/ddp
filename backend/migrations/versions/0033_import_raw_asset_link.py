"""导入产出接入 Canonical 层：MediaAsset.raw_asset_id + RawAsset 五列补齐

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-15

背景（N0 审计主链断点）
----------------------
HF 导入链路的产物止步于 ``data_source_media_assets``，从不产生 ``RawAsset``。
而 Episode 创建强制要求 ``raw_asset_ids``（``min_length=1``），

    导入 -> MediaAsset -> ✗ -> RawAsset -> ✗ -> Episode

于是**导入的真实数据无法进入标注/质检/数据集/训练的任何一环**。实测：
``media_assets=384`` 而 ``raw_assets=0``、``episodes=0``。

本迁移做两件事：

1. ``data_source_media_assets`` 增加 ``raw_asset_id``，指向对应的 Canonical
   原始资产。新导入必填；历史数据留空（对象已存在但无 RawAsset 记录，
   回填需重新下载并重算 Hash，不在本迁移内做数据迁移）。

2. ``raw_assets`` 补齐五列：``device_ids`` / ``sensor_ids`` /
   ``sensor_snapshot`` / ``device_time_raw`` / ``raw_metadata``。
   这五列在**领域模型与契约里早已存在**（``domain/ingestion/models.py:350-352``、
   ``packages/contracts/fragments/schemas.stage2.yaml:301`` 把 ``raw_metadata``
   列为 required），API 出参也声明了，唯独 ORM 没有列——落库时被静默丢弃，
   实际永远是空。这是既有契约违约，N2 的 ADP-004「原始字段不丢失」正卡在这里。

为什么 ``raw_asset_id`` 允许 NULL
--------------------------------
历史 384 条资产产生时 RawAsset 概念还没接入。若设 NOT NULL，迁移会直接失败。
两种做法：回填（需重新下载 31MB×12 并重算 sha256，且要造 RawAsset 行）或允许
历史为空。选后者——它是**如实的**：那些行确实没有对应的 RawAsset 记录。
新导入由 ``import_runner`` 强制填，并在测试里断言。

回滚
----
``downgrade`` 严格逆向：删索引、删外键列、删五列。可反复升降级。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. MediaAsset -> RawAsset 外键 -------------------------------------
    op.add_column(
        "data_source_media_assets",
        sa.Column("raw_asset_id", sa.Uuid(), nullable=True),
    )
    # ondelete=RESTRICT：RawAsset 有禁删触发器，这里保持一致——不允许
    # 通过删 RawAsset 让媒体资产悬空。SQLite 不支持给已有表加外键，
    # 故仅在 PostgreSQL 上建约束（SQLite 测试库靠 create_all 建表时自带）。
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_media_assets_raw_asset_id_raw_assets",
            "data_source_media_assets",
            "raw_assets",
            ["raw_asset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_media_assets_raw_asset",
        "data_source_media_assets",
        ["raw_asset_id"],
    )

    # --- 2. RawAsset 五列补齐 ----------------------------------------------
    op.add_column(
        "raw_assets",
        sa.Column(
            "device_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "raw_assets",
        sa.Column(
            "sensor_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column("raw_assets", sa.Column("sensor_snapshot", sa.JSON(), nullable=True))
    op.add_column("raw_assets", sa.Column("device_time_raw", sa.JSON(), nullable=True))
    op.add_column(
        "raw_assets",
        sa.Column(
            "raw_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("raw_assets", "raw_metadata")
    op.drop_column("raw_assets", "device_time_raw")
    op.drop_column("raw_assets", "sensor_snapshot")
    op.drop_column("raw_assets", "sensor_ids")
    op.drop_column("raw_assets", "device_ids")

    op.drop_index("ix_media_assets_raw_asset", table_name="data_source_media_assets")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_media_assets_raw_asset_id_raw_assets",
            "data_source_media_assets",
            type_="foreignkey",
        )
    op.drop_column("data_source_media_assets", "raw_asset_id")
