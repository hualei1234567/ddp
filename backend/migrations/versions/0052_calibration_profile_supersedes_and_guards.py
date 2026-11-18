"""标定配置版本链落库 + 重装标注工作守卫

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-18

两件事：

1. ``calibration_profiles.supersedes_id``。创建接口一直会校验这个字段（同作用域、
   同目标、版本号 +1），但**从未落库**：库里查不到"谁替代了谁"，既画不出配置
   版本链，也拦不住同一父版本被两条分支各自继承（分叉）。现在加列 + 自引用
   复合外键（按 project_id 隔离），并把"已有后继"做成创建时的拒绝条件。

2. 重装 ``annotation_work_guards``。守卫 DDL 修正了「已取消批次导致任务永远
   无法提交」：SQLite 用 ``CREATE TRIGGER IF NOT EXISTS`` 安装，规则变了不会
   自动生效，必须显式 drop + recreate。

3. 顺带重装 ``calibration_profiles`` 的不可变守卫。SQLite 的
   ``batch_alter_table`` 会重建整张表（create new → copy → drop old → rename），
   表上的触发器随旧表一起消失；不重装的话，「配置行不可更新/不可删除」这条
   数据库级约束在升级后就没了。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_work_guards import (
    drop_work_guards,
    install_work_guards,
)
from app.infrastructure.db.guards import (
    drop_calibration_profile_guards,
    install_calibration_profile_guards,
)

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def _has_supersedes() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "supersedes_id" in {
        column["name"] for column in inspector.get_columns("calibration_profiles")
    }


def _reinstall_profile_guards() -> None:
    # 触发器随表重建被删掉了；先 drop 再 install，两种方言都幂等。
    drop_calibration_profile_guards(op.get_bind())
    install_calibration_profile_guards(op.get_bind())


def upgrade() -> None:
    if not _has_supersedes():
        with op.batch_alter_table("calibration_profiles") as batch:
            batch.add_column(sa.Column("supersedes_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_calibration_profiles_project_supersedes",
                "calibration_profiles",
                ["project_id", "supersedes_id"],
                ["project_id", "id"],
            )
        _reinstall_profile_guards()
    install_work_guards(op.get_bind())


def downgrade() -> None:
    drop_work_guards(op.get_bind())
    install_work_guards(op.get_bind())
    if _has_supersedes():
        with op.batch_alter_table("calibration_profiles") as batch:
            batch.drop_constraint("fk_calibration_profiles_project_supersedes", type_="foreignkey")
            batch.drop_column("supersedes_id")
        _reinstall_profile_guards()
