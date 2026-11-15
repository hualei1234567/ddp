"""calibration profile immutability guards

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-15

ADR-0020 / G-07：``calibration_profiles`` 行不可变。

Profile 是"配置的某一版本"；改配置的正确做法是插入新版本行。没有这条约束时，
直接 SQL 改一行就能让历史记录的 ``profile_version`` 指向一段已经不同的配置，
谱系不可信。

禁止 UPDATE 与 DELETE：
- UPDATE 会让已固化的快照与实际配置脱节；
- DELETE 会让历史记录的 ``profile_id`` 悬空。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.guards import (
    drop_calibration_profile_guards,
    install_calibration_profile_guards,
)

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_calibration_profile_guards(op.get_bind())


def downgrade() -> None:
    drop_calibration_profile_guards(op.get_bind())
