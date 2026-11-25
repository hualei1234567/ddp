"""V52-009：标注员技能标签表 + 任务技能路由配置列

Revision ID: 0080
Revises: 0074
Create Date: 2026-11-25

内容
----
1. 新表 ``annotator_skill_tags``：标注员技能标签事实表
   （一行 = 一名项目成员持有一个规范化标签），
   唯一约束 ``(project_id, annotator_id, tag_name)``；
2. ``annotation_tasks.required_skills`` JSON 列（nullable，默认 NULL）：
   任务侧技能路由配置；NULL = 不启用技能路由（既有分配逻辑 100% 不变）。

> 分支说明：本迁移在 feat/v52-skill-routing 上基于 main（0074）创建。
> 任务卡指定编号 0080 且禁止改 0078/0079/0081——那三个编号属于并行
> agent 的迁移（合入时由主控按时间线串接 down_revision）。本迁移的
> ``down_revision`` 在主线合入时需要由主控改为当时的 head（如 0079），
> 本分支内保持 0074 以保证迁移测试可独立跑通。

方言策略（MEM-INC-013）
----------------------
- **SQLite**（``tests/migrations/`` 的既有约定）：``op.create_table`` /
  ``op.add_column`` 都是通用 DDL，直接执行；
- **PostgreSQL**（生产/集成）：所有语句显式限定 ``public.``——
  历史测试库的同名表/约束可能分属其他 schema（``ddp_v51xxx`` 等），
  不限定会误操作 search_path 命中的那张。

**SQLite 删列必须先卸载跨表守卫触发器**：``annotation_tasks`` 被
catalog / work / result 三组守卫触发器的**正文**引用（子查询
``SELECT ... FROM annotation_tasks``），0041 已实测——batch 重建表的
改名瞬间 SQLite 会解析所有引用该表的触发器，触发器存在即报
``error in trigger ...: no such table``。因此 downgrade 在 batch 重建前
``drop_catalog/result/work_guards``、重建后重装（0052 同一模式，
drop/install 均幂等，两种方言通用）。upgrade 只建新表加列，不重建
``annotation_tasks``，无需动触发器。

``required_skills`` 列删除在 SQLite 上走 ``batch_alter_table``；
``recreate="auto"`` 复制的既有 CHECK 均为同名单约束，重建后天然保留，
无需手工去重；PG 侧 ``DROP COLUMN`` 不触碰 CHECK。本迁移不修改任何
枚举约束，MEM-INC-012 的多表同步陷阱不适用。

回滚
----
``downgrade`` 删列、删表。技能标签是**登记型事实数据**（人员会什么），
可从上游治理记录重建；回滚不保留这些行——与所有"新增能力"迁移一致，
回滚后回到 V52-009 之前的形态。若已有行使用 ``required_skills``，
回滚后这些任务的技能路由自然失效（回到不路由），不构成数据丢失：
分配是过程数据，技能配置是可重建配置。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_catalog_guards import (
    drop_catalog_guards,
    install_catalog_guards,
)
from app.infrastructure.db.annotation_result_guards import (
    drop_result_guards,
    install_result_guards,
)
from app.infrastructure.db.annotation_work_guards import (
    drop_work_guards,
    install_work_guards,
)

revision = "0080"
down_revision = "0074"
branch_labels = None
depends_on = None

_TASK_TABLE = "annotation_tasks"
_SKILL_TABLE = "annotator_skill_tags"
_TASK_COLUMN = "required_skills"


def _dialect() -> str:
    """当前连接的数据库方言名（``postgresql`` / ``sqlite``）。"""
    return op.get_bind().dialect.name


def _relation(table: str) -> str:
    """表引用：PG 显式限定 ``public.``（MEM-INC-013 踩坑 1），
    SQLite 不能加前缀（没有 schema 概念，加了报 no such table）。
    """
    if _dialect() == "postgresql":
        return f"public.{table}"
    return table


def _drop_cross_table_guards() -> None:
    """卸载引用 ``annotation_tasks`` 的三组守卫触发器（SQLite 删列前置）。

    batch 重建的改名瞬间 SQLite 解析所有引用该表的触发器正文，任何一组
    在位都会报 ``error in trigger ...: no such table``（0041 实测）。
    PG 无此问题但 drop/install 幂等，统一调用无害。
    """
    drop_catalog_guards(op.get_bind())
    drop_result_guards(op.get_bind())
    drop_work_guards(op.get_bind())


def _install_cross_table_guards() -> None:
    """重装三组守卫触发器（各 install 内部自带表存在性检查，幂等）。"""
    install_catalog_guards(op.get_bind())
    install_result_guards(op.get_bind())
    install_work_guards(op.get_bind())


def upgrade() -> None:
    op.create_table(
        _SKILL_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("annotator_id", sa.Uuid(), nullable=False),
        sa.Column("tag_name", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_annotator_skill_tags"),
        sa.UniqueConstraint(
            "project_id",
            "annotator_id",
            "tag_name",
            name="uq_annotator_skill_tags_project_annotator_tag",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotator_skill_tags_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotator_skill_tags_project_annotator",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_annotator_skill_tags_created_by",
        ),
        sa.CheckConstraint("length(tag_name) >= 1", name="tag_name_non_empty"),
    )
    op.create_index(
        "ix_annotator_skill_tags_project_annotator",
        _SKILL_TABLE,
        ["project_id", "annotator_id"],
    )
    op.create_index(
        "ix_annotator_skill_tags_project_tag",
        _SKILL_TABLE,
        ["project_id", "tag_name"],
    )

    # nullable、无 server_default 语义层默认：NULL = 不路由（零配置兼容）。
    # PG 与 SQLite 的 ADD COLUMN 都是通用 DDL，无需分支。
    op.add_column(
        _TASK_TABLE,
        sa.Column(_TASK_COLUMN, sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # 删列前置：annotation_tasks 的触发器正文引用它（catalog/work/result
    # 三组），batch 重建的改名瞬间会解析触发器而报错（0041 实测）。
    # 先卸载、batch 删列、再重装——与 0052 的 profile 守卫重装同一模式。
    _drop_cross_table_guards()
    with op.batch_alter_table(_TASK_TABLE, recreate="auto") as batch:
        batch.drop_column(_TASK_COLUMN)
    _install_cross_table_guards()

    # PG 侧先删索引再删表，且全部限定 public.；SQLite 的 DROP TABLE
    # 自带索引清理。避免依赖方言隐式行为，两侧各走分支。
    if _dialect() == "postgresql":
        op.execute(
            "DROP INDEX IF EXISTS public.ix_annotator_skill_tags_project_tag"  # noqa: S608 - 常量内联
        )
        op.execute(
            "DROP INDEX IF EXISTS public.ix_annotator_skill_tags_project_annotator"  # noqa: S608
        )
        op.execute(f"DROP TABLE IF EXISTS public.{_SKILL_TABLE}")  # noqa: S608
    else:
        op.drop_index("ix_annotator_skill_tags_project_tag", table_name=_SKILL_TABLE)
        op.drop_index("ix_annotator_skill_tags_project_annotator", table_name=_SKILL_TABLE)
        op.drop_table(_SKILL_TABLE)
