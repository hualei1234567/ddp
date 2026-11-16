"""COL-002 collection plans / SOPs / scenarios / action specs + task foreign keys

Revision ID: 0040
Revises: 0032
Create Date: 2026-11-16

问题（审计结论，不是推测）
--------------------------
迁移 0007 给 ``collection_tasks`` 建了 ``plan_id`` / ``sop_id`` / ``scenario_id`` /
``action_spec_id`` / ``sop_version`` / ``action_spec_version`` 六个可空列，
但**从未建过对应的四张表，也没有任何外键**（``grep collection_plans migrations/`` 零命中）。
后果：这四个 UUID 列可以写入任意值——跨项目 ID、根本不存在的"幽灵引用"，
数据库层完全无法拒绝，只能靠调用方自觉。

本迁移做两件事：

1. 建立 ``collection_plans`` / ``collection_sops`` / ``scenarios`` / ``action_specs``
   四张表，全部带 ``(project_id, id)`` 复合唯一键与项目隔离；
2. 给 ``collection_tasks`` 的四个引用列补 ``(project_id, xxx_id)`` **复合外键**，
   让"同项目"成为数据库可判定的事实。

版本语义（六个字段成对理解）
----------------------------
- ``plan_id`` → 方案行；``plan_version`` 在方案表上，按整型版本演进；
- ``sop_id`` → SOP 行，``sop_version`` 是**任务创建时冻结的 SOP 版本快照**：
  SOP 升版 = 新增 ``collection_sops`` 行（``(project_id, code, sop_version)`` 唯一），
  历史任务**不**被改写；
- ``action_spec_id`` + ``action_spec_version`` 同理；
- ``scenario_id`` 无版本列（场景不版本化）。

六个列全部保持 ``nullable``：存量任务没有这些引用，强制非空会让升级直接失败。

为什么用 ``batch_alter_table``（SQLite 的硬约束）
--------------------------------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，加外键只能"建新表 + 拷数据 +
换名"。``recreate="auto"`` 在 SQLite 上等价于 always，在 PostgreSQL 上走原生
``ALTER TABLE ADD CONSTRAINT``（不重建表）。同一份代码覆盖两方言，避免
"本地 SQLite 绿、真实 PG 炸"这类只有上线才发现的分叉。

⚠️ SQLite 上的一个真实陷阱（已实测，失败信息为
``error in trigger trg_collection_batches_transition: no such table: main.collection_tasks``）：
batch 重建会先 ``ALTER TABLE collection_tasks RENAME TO _alembic_tmp_collection_tasks``，
而迁移 0007 装在 ``collection_batches`` / ``collection_task_devices`` /
``collection_task_sensors`` 上的触发器正文里引用了 ``collection_tasks``，
SQLite 在改名那一刻解析触发器失败，整个迁移中止。
因此升级时**先** ``drop_collection_state_guards``，重建完成后再
``install_collection_state_guards`` 装回。守卫模块是既有实现（``Base.metadata``
的 ``after_create`` 与迁移 0007 共用同一份 DDL），不在这里另写一份。

存量悬空引用必须先清掉（否则升级直接失败）
------------------------------------------
本 bug 的存在意味着**真实库里很可能已经有脏数据**：四个列从 0007 起就能写入
任意 UUID。给这样的表加外键，数据库会在 ``ADD CONSTRAINT`` 那一刻校验存量行，
一条悬空引用就让整个升级回滚（已在真实 PostgreSQL 上复现，报
``ForeignKeyViolation ... is not present in table "collection_plans"``）。

处理策略（与迁移 0027/0028 的回填范式一致：**做、但要留痕**）：

- 四张表本迁移才建、必定为空 → 存量任务的四个引用**不可能**指向有效素材，
  只可能是幽灵引用，因此置 ``NULL``；
- 置空而不是报错中止：升级被一条历史脏数据卡死时，运维只能手工改库，
  风险比"迁移自动清理 + 逐行留痕"更高；
- ``sop_version`` / ``action_spec_version`` 一并清空——ID 没了还留版本号
  是半截引用，比空值更误导；
- 每一行被清理的任务 ID 都通过 ``logging`` 输出到 alembic 的运行日志
  （``ddp.migration.0040``），可据此审计与追认，不是静默吞掉。

回滚
----
``downgrade`` 是 upgrade 的严格逆操作：先卸守卫、按逆序删四条外键、
再装回守卫，最后按依赖逆序删四张表（被引用者先删引用者）。

⚠️ 回滚**不恢复**被清理的悬空引用。这是有意的：那些值本就是无效引用，
恢复它们等于恢复缺陷。需要追溯时以本迁移的执行日志为准。
"""

from __future__ import annotations

# ruff: noqa: S608 - 清理语句的列名只来自本模块常量，不接收任何外部输入；
# 与 ``collection_guards.py`` 的既有做法一致（那里也是常量拼 DDL）。
import logging

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_collection_state_guards,
    install_collection_state_guards,
)

revision = "0040"
down_revision = "0038"
branch_labels = None
depends_on = None

logger = logging.getLogger("ddp.migration.0040")

_DESCRIPTION_LENGTH = 2000
_STATUS_VALUES = "'DRAFT', 'ACTIVE', 'ARCHIVED'"

# 素材表 -> 任务上的引用列；顺序即外键创建顺序，downgrade 逆序使用。
_TASK_ASSET_FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("plan_id", "collection_plans", "fk_collection_tasks_project_plan"),
    ("sop_id", "collection_sops", "fk_collection_tasks_project_sop"),
    ("scenario_id", "scenarios", "fk_collection_tasks_project_scenario"),
    ("action_spec_id", "action_specs", "fk_collection_tasks_project_action_spec"),
)


def _audit_columns() -> list[sa.Column]:
    """与 ``models/collection.py`` 的 ``ProjectScopedAuditMixin`` 逐列一致。"""
    return [
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def _audit_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
    ]


def _status_and_version_checks() -> list[sa.schema.Constraint]:
    return [
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    ]


def upgrade() -> None:
    op.create_table(
        "collection_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=_DESCRIPTION_LENGTH), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_collection_plans"),
        sa.UniqueConstraint("project_id", "id", name="uq_collection_plans_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "code",
            "plan_version",
            name="uq_collection_plans_project_code_version",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_collection_plans_owner_id_users",
        ),
        *_status_and_version_checks(),
        sa.CheckConstraint("plan_version >= 1", name="plan_version_positive"),
        *_audit_constraints("collection_plans"),
    )
    op.create_index(
        "ix_collection_plans_project_status",
        "collection_plans",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_collection_plans_project_code",
        "collection_plans",
        ["project_id", "code"],
    )

    op.create_table(
        "collection_sops",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sop_version", sa.String(length=64), nullable=False),
        sa.Column("steps", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=_DESCRIPTION_LENGTH), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_collection_sops"),
        sa.UniqueConstraint("project_id", "id", name="uq_collection_sops_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "code",
            "sop_version",
            name="uq_collection_sops_project_code_version",
        ),
        *_status_and_version_checks(),
        *_audit_constraints("collection_sops"),
    )
    op.create_index(
        "ix_collection_sops_project_status",
        "collection_sops",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_collection_sops_project_code",
        "collection_sops",
        ["project_id", "code"],
    )

    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scene_labels", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=_DESCRIPTION_LENGTH), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_scenarios"),
        sa.UniqueConstraint("project_id", "id", name="uq_scenarios_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_scenarios_project_code"),
        *_status_and_version_checks(),
        *_audit_constraints("scenarios"),
    )
    op.create_index("ix_scenarios_project_status", "scenarios", ["project_id", "status"])

    op.create_table(
        "action_specs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("spec_version", sa.String(length=64), nullable=False),
        sa.Column("actions", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=_DESCRIPTION_LENGTH), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_action_specs"),
        sa.UniqueConstraint("project_id", "id", name="uq_action_specs_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "code",
            "spec_version",
            name="uq_action_specs_project_code_version",
        ),
        *_status_and_version_checks(),
        *_audit_constraints("action_specs"),
    )
    op.create_index(
        "ix_action_specs_project_status",
        "action_specs",
        ["project_id", "status"],
    )
    op.create_index("ix_action_specs_project_code", "action_specs", ["project_id", "code"])

    _clear_dangling_task_references()
    _add_task_asset_foreign_keys()


_DANGLING_REFERENCE_COLUMNS: tuple[str, ...] = (
    "plan_id",
    "sop_id",
    "scenario_id",
    "action_spec_id",
)
# 版本快照列：单独出现的版本号是"半截引用"（ID 为空、版本号残留），
# 应用层创建任务时已拒绝这种组合（见 ``asset_references``），存量的同类脏数据
# 也必须一起清掉——否则任务带着"冻结了某版本但不知道是哪份素材"的状态存续。
_DANGLING_VERSION_COLUMNS: tuple[str, ...] = ("sop_version", "action_spec_version")


def _clear_dangling_task_references() -> None:
    """清空存量任务上的素材引用（含半截版本快照），并逐行留痕。

    四张素材表在本迁移刚建，必然为空，所以存量任务的任何非空引用都是悬空值
    （这正是 COL-002 缺陷的后果）。不清掉的话 ``ADD CONSTRAINT`` 会在存量行上
    校验失败，整个升级回滚。

    为什么无条件清空而不是"只清无法解析的"：本迁移执行时刻，素材表里不可能有
    任何行可以解析——是"全部悬空"而不是"部分悬空"。写成条件更新会给人
    "有些引用是有效的"的错觉，反而掩盖事实。

    命中条件分两类，都必须清：
    - 四个引用列任一非空 → 悬空引用（外键会拒绝）；
    - 两个版本列任一非空 → 半截引用（ID 为空但留了版本号，语义自相矛盾）。

    SQLite 与 PostgreSQL 都能处理 ``WHERE col IS NOT NULL``；先用 SELECT 拿到
    受影响的任务 ID 逐行记日志，再用一条 UPDATE 完成，避免 N+1 的迁移耗时。
    """
    connection = op.get_bind()
    dirty_condition = " OR ".join(
        f"{column} IS NOT NULL"
        for column in (*_DANGLING_REFERENCE_COLUMNS, *_DANGLING_VERSION_COLUMNS)
    )
    affected = connection.execute(
        sa.text(f"SELECT id FROM collection_tasks WHERE {dirty_condition}")
    ).fetchall()
    if not affected:
        logger.info("COL-002：存量任务无悬空/半截素材引用，无需清理")
        return

    for (task_id,) in affected:
        logger.warning(
            "COL-002：清空任务 %s 上的悬空或半截素材引用（素材表在 0040 后才存在，"
            "此前的引用无法解析，原值不予保留）",
            task_id,
        )
    cleared_columns = (*_DANGLING_REFERENCE_COLUMNS, *_DANGLING_VERSION_COLUMNS)
    connection.execute(
        sa.text(
            "UPDATE collection_tasks SET "
            + ", ".join(f"{column} = NULL" for column in cleared_columns)
            + f" WHERE {dirty_condition}"
        )
    )
    logger.warning("COL-002：共清理 %d 条任务的悬空/半截素材引用", len(affected))


def _add_task_asset_foreign_keys() -> None:
    """给 ``collection_tasks`` 补四条复合外键。

    移除守卫 → batch 重建 → 装回守卫。顺序不可调换：守卫触发器正文引用
    ``collection_tasks``，表被临时改名时 SQLite 解析触发器会失败。
    """
    connection = op.get_bind()
    drop_collection_state_guards(connection)
    with op.batch_alter_table("collection_tasks", recreate="auto") as batch:
        for column, table, name in _TASK_ASSET_FOREIGN_KEYS:
            batch.create_foreign_key(
                name,
                table,
                ["project_id", column],
                ["project_id", "id"],
                ondelete="RESTRICT",
            )
    install_collection_state_guards(connection)


def _drop_task_asset_foreign_keys() -> None:
    connection = op.get_bind()
    drop_collection_state_guards(connection)
    with op.batch_alter_table("collection_tasks", recreate="auto") as batch:
        for _column, _table, name in reversed(_TASK_ASSET_FOREIGN_KEYS):
            batch.drop_constraint(name, type_="foreignkey")
    install_collection_state_guards(connection)


def downgrade() -> None:
    _drop_task_asset_foreign_keys()

    op.drop_index("ix_action_specs_project_code", table_name="action_specs")
    op.drop_index("ix_action_specs_project_status", table_name="action_specs")
    op.drop_table("action_specs")

    op.drop_index("ix_scenarios_project_status", table_name="scenarios")
    op.drop_table("scenarios")

    op.drop_index("ix_collection_sops_project_code", table_name="collection_sops")
    op.drop_index("ix_collection_sops_project_status", table_name="collection_sops")
    op.drop_table("collection_sops")

    op.drop_index("ix_collection_plans_project_code", table_name="collection_plans")
    op.drop_index("ix_collection_plans_project_status", table_name="collection_plans")
    op.drop_table("collection_plans")
