"""calibration target refs and immutability guards

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08

修复 QA-205 D-205-01/02：

1. 新增 ``calibration_target_refs`` 多态投影表，并为 ``calibration_records``
   建立 ``(project_id, target_type, target_id)`` 复合外键，拒绝不存在、
   跨项目或类型错配的目标；SQLite 不支持给既有表追加外键，改用等价触发器。
2. 安装不可变触发器，拦截 VALIDATED/ACTIVE/SUPERSEDED/REVOKED/EXPIRED
   记录的 checksum、状态与溯源字段直写；只放行契约 §8 的合法状态迁移。

迁移前若存在无法解析到投影目标的标定记录，迁移会失败并回滚，要求先修复
数据；不会静默生成幽灵目标。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.guards import (
    drop_calibration_record_guards,
    drop_calibration_target_guards,
    install_calibration_record_guards,
    install_calibration_target_guards,
)

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TARGET_TYPE_VALUES = "'ROBOT', 'DEVICE', 'SENSOR', 'SYSTEM'"
_LIFECYCLE_VALUES = "'PROVISIONED', 'ACTIVE', 'IN_MAINTENANCE', 'FAULT', 'SUSPENDED', 'RETIRED'"

_BACKFILL_STATEMENTS = (
    """
    INSERT INTO calibration_target_refs
        (project_id, target_type, target_id, lifecycle_status, is_retired, updated_at)
    SELECT project_id, 'ROBOT', id, status,
           CASE WHEN status = 'RETIRED' THEN TRUE ELSE FALSE END,
           CURRENT_TIMESTAMP
    FROM robots
    """,
    """
    INSERT INTO calibration_target_refs
        (project_id, target_type, target_id, lifecycle_status, is_retired, updated_at)
    SELECT project_id, 'DEVICE', id, status,
           CASE WHEN status = 'RETIRED' THEN TRUE ELSE FALSE END,
           CURRENT_TIMESTAMP
    FROM devices
    """,
    """
    INSERT INTO calibration_target_refs
        (project_id, target_type, target_id, lifecycle_status, is_retired, updated_at)
    SELECT project_id, 'SENSOR', id, status,
           CASE WHEN status = 'RETIRED' THEN TRUE ELSE FALSE END,
           CURRENT_TIMESTAMP
    FROM sensors
    """,
    """
    INSERT INTO calibration_target_refs
        (project_id, target_type, target_id, lifecycle_status, is_retired, updated_at)
    SELECT id, 'SYSTEM', id, 'ACTIVE', FALSE, CURRENT_TIMESTAMP
    FROM projects
    """,
)

_ORPHAN_CHECK = """
SELECT COUNT(*)
FROM calibration_records record
WHERE NOT EXISTS (
    SELECT 1
    FROM calibration_target_refs ref
    WHERE ref.project_id = record.project_id
      AND ref.target_type = record.target_type
      AND ref.target_id = record.target_id
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.create_table(
        "calibration_target_refs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
        sa.Column(
            "is_retired",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "target_type",
            "target_id",
            name="pk_calibration_target_refs",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_calibration_target_refs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"target_type IN ({_TARGET_TYPE_VALUES})",
            name="ck_calibration_target_refs_target_type_allowed",
        ),
        sa.CheckConstraint(
            f"lifecycle_status IN ({_LIFECYCLE_VALUES})",
            name="ck_calibration_target_refs_lifecycle_status_allowed",
        ),
        sa.CheckConstraint(
            "is_retired = (lifecycle_status = 'RETIRED')",
            name="ck_calibration_target_refs_retired_flag_consistent",
        ),
        sa.CheckConstraint(
            "target_type <> 'SYSTEM' OR target_id = project_id",
            name="ck_calibration_target_refs_system_target_matches_project",
        ),
    )

    for statement in _BACKFILL_STATEMENTS:
        bind.execute(sa.text(statement))

    orphans = bind.execute(sa.text(_ORPHAN_CHECK)).scalar_one()
    if orphans:
        raise RuntimeError(f"存在 {orphans} 条标定记录的目标不在投影表中，请先修复数据再执行 0004")

    if dialect == "postgresql":
        op.create_foreign_key(
            "fk_calibration_records_project_target_ref",
            "calibration_records",
            "calibration_target_refs",
            ["project_id", "target_type", "target_id"],
            ["project_id", "target_type", "target_id"],
            ondelete="RESTRICT",
        )
    else:
        # SQLite 无法 ALTER TABLE ADD CONSTRAINT；用等价的 BEFORE INSERT/UPDATE
        # 触发器保证目标投影行存在。PostgreSQL 始终使用真实复合外键。
        install_calibration_target_guards(bind)

    install_calibration_record_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()

    drop_calibration_record_guards(bind)

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_calibration_records_project_target_ref",
            "calibration_records",
            type_="foreignkey",
        )
    else:
        drop_calibration_target_guards(bind)

    op.drop_table("calibration_target_refs")
