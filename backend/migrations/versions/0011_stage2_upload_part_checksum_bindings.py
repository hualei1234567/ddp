"""stage2 upload part checksum bindings

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09

持久化分片预签名 URL 声明的 ``x-amz-checksum-sha256`` 绑定值。预签名请求
可能早于分片 ack，绑定必须跨请求保存；ack 时据此拒绝与 URL 不一致的内容。
重复签名同一分片时以最新绑定为准。本迁移只新增辅助表，不回填历史数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_SHA256_CHECK = "length(sha256) = 71 AND substr(sha256, 1, 7) = 'sha256:'"


def upgrade() -> None:
    op.create_table(
        "upload_part_checksum_bindings",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "project_id",
            "upload_session_id",
            "part_number",
            name="pk_upload_part_checksum_bindings",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "upload_session_id"],
            ["upload_sessions.project_id", "upload_sessions.id"],
            name="fk_upload_part_checksum_bindings_project_session",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "part_number BETWEEN 1 AND 10000",
            name=op.f("ck_upload_part_checksum_bindings_part_number_range"),
        ),
        sa.CheckConstraint(
            _SHA256_CHECK,
            name=op.f("ck_upload_part_checksum_bindings_sha256_format"),
        ),
    )


def downgrade() -> None:
    op.drop_table("upload_part_checksum_bindings")
