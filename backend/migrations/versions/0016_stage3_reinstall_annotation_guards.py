"""stage3 reinstall annotation guards

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09

DDP-320 发现已迁移库中的标注守卫函数仍包含 PostgreSQL JSON `<>` 比较。
守卫 DDL 由应用模块生成，``create_all`` 会安装新版本，但已处于 0015 的库
执行 ``upgrade head`` 不会重装。本迁移显式重装四组守卫，确保升级后
函数体与代码一致。

downgrade 不恢复旧缺陷函数体：守卫属于安全兜底，回滚保持当前版本。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.annotation_catalog_guards import install_catalog_guards
from app.infrastructure.db.annotation_quality_guards import install_quality_guards
from app.infrastructure.db.annotation_result_guards import install_result_guards
from app.infrastructure.db.annotation_work_guards import install_work_guards

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    install_catalog_guards(connection)
    install_work_guards(connection)
    install_result_guards(connection)
    install_quality_guards(connection)


def downgrade() -> None:
    # 守卫函数不属于表结构；保留修复后的函数体，避免回滚重新引入 JSON `<>`。
    pass
