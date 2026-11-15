"""calibration version split and profile snapshot

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-15

ADR-0020：G-04 / G-05 / G-06。

- G-04：新增 `calibration_version`（业务修订号），`version` 继续作为乐观锁；
- G-05：记录固化创建时的 Profile 快照（版本 / schema / 内容副本）；
- G-06：Profile 新增 `target_id` 与版本唯一键。

回填说明：既有记录没有历史快照，用**当前** Profile 值回填，并在
`profile_snapshot` 里标 `{"backfilled": true}`——不假装它们当时就是这样。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "calibration_records",
        sa.Column("calibration_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("calibration_records", sa.Column("profile_version", sa.Integer(), nullable=True))
    op.add_column(
        "calibration_records", sa.Column("profile_schema_version", sa.String(64), nullable=True)
    )
    op.add_column(
        "calibration_records",
        sa.Column(
            "profile_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # 回填：用当前 Profile 值，并显式标注这不是历史快照。
    bind.execute(
        sa.text(
            """
            UPDATE calibration_records
               SET profile_version = (
                       SELECT p.version FROM calibration_profiles p
                        WHERE p.id = calibration_records.profile_id
                   ),
                   profile_schema_version = (
                       SELECT p.schema_version FROM calibration_profiles p
                        WHERE p.id = calibration_records.profile_id
                   ),
                   profile_snapshot = '{"backfilled": true}'
            """
        )
    )
    op.add_column("calibration_profiles", sa.Column("target_id", sa.Uuid(), nullable=True))
    # 唯一键在 PG 与 SQLite 都支持（CREATE UNIQUE INDEX 不是 ALTER 约束）。
    op.create_index(
        "uq_calibration_profiles_name_target_version",
        "calibration_profiles",
        ["project_id", "name", "target_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_calibration_profiles_name_target_version", table_name="calibration_profiles")
    op.drop_column("calibration_profiles", "target_id")
    op.drop_column("calibration_records", "profile_snapshot")
    op.drop_column("calibration_records", "profile_schema_version")
    op.drop_column("calibration_records", "profile_version")
    # 注意：回滚会丢失 calibration_version 的增量历史（原 version 从未记录过它）。
    # 这是有意接受的代价；如需保留，应在回滚前导出该列。
    op.drop_column("calibration_records", "calibration_version")
