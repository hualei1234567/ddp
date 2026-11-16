"""N9 多基地：建立 ``locations`` 表并给三处 ``location_id`` 补复合外键

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-16

问题（审计结论，不是推测）
--------------------------
``robots`` / ``devices``（迁移 0003）与 ``collection_tasks``（迁移 0007）各有
一列 ``location_id UUID NULL``，但**从来没有 ``locations`` 表，也没有任何外键**
（``grep locations migrations/`` 零命中）。后果与 COL-002 完全同型：这个 UUID
可以写入任意值——跨项目 ID、根本不存在的"幽灵引用"，数据库层无法拒绝，
只能靠调用方自觉。

也就是说，"这个机器人部署在哪个基地""这批采集在哪个场地做的"这两个问题，
系统目前无法可靠回答。本迁移做三件事：

1. 建立 ``locations`` 表（项目隔离 + ``(project_id, id)`` 复合唯一键）；
2. 清理三张表上**存量的悬空/跨项目** ``location_id`` 并逐行留痕；
3. 给三处引用补 ``(project_id, location_id) → locations(project_id, id)``
   **复合外键**，让"同项目"成为数据库可判定的事实。

为什么可空列必须放行 NULL
------------------------
``location_id`` 保持 ``nullable``：存量行没有场地信息，强制非空会让升级直接
失败；且"尚未指定基地"是合法业务状态（例如新到货、尚未分配场地的机器人）。
SQL 复合外键的语义天然满足这一点——只要任一列或全部引用列为 NULL，约束即
视为满足。因此**不是**"用 NULL 绕过校验"，而是 NULL 本身就代表"未指定"，
与应用层"NULL 与不存在严格区分"的口径一致。

为什么外键用 ``ON DELETE NO ACTION``（而非 0040 的 ``RESTRICT``）
------------------------------------------------------------------
两者都拒绝删除被引用的场地，差别只在检查时机：``RESTRICT`` 立即检查，
``NO ACTION`` 在同一语句结束时检查。本表的 ``project_id`` 是
``ON DELETE CASCADE``，删除项目时数据库会级联删除 ``robots`` / ``devices`` /
``locations`` 三者；若用 ``RESTRICT``，级联的执行顺序会让"先删 locations、
后删 robots"的合法级联被立即拒绝，等于**新引入**一个删除项目的故障。
``NO ACTION`` 在语句末尾校验，此时两侧行都已消失，级联行为与迁移前一致。
（已在真实 PostgreSQL 上对两种写法做过对比验证。）

存量悬空引用必须先清掉（否则升级直接失败）
------------------------------------------
本 bug 的存在意味着真实库里很可能已经有脏数据：三列从 0003 / 0007 起就能写入
任意 UUID。给这样的表加外键，数据库会在 ``ADD CONSTRAINT`` 那一刻校验存量行，
一条悬空引用就让整个升级回滚（0040 已在真实 PostgreSQL 上复现同类失败）。

处理策略（与迁移 0040 的范式一致：**做、但要留痕**）：

- 判定条件是**精确**的：``location_id`` 非空且在本项目内找不到对应场地行。
  ``locations`` 是本迁移刚建的空表，所以实际上所有非空值都会被命中——但把条件
  写成"存在性判定"而不是"无条件清空"，是因为被加的外键约束正是这条判定；
  条件与约束一旦不一致，就会变成"迁移放过了、约束又拒绝"的死循环；
- 置 ``NULL`` 而不是报错中止：升级被一条历史脏数据卡死时，运维只能手工改库，
  风险比"迁移自动清理 + 逐行留痕"更高；
- 每一行被清理的**表名、行 ID、被丢弃的 location_id 原值**都通过 ``logging``
  输出到 alembic 运行日志（``ddp.migration.0041``）。原值必须记录：它是唯一
  能追溯"这台机器人自认为在哪个场地"的线索，只记行 ID 会让追认无从下手。
  日志只写服务端日志，不回传客户端。

⚠️ 回滚**不恢复**被清理的悬空引用。这是有意的：那些值本就是无效引用，
恢复它们等于恢复缺陷。需要追溯时以本迁移的执行日志为准。

为什么用 ``batch_alter_table``（SQLite 的硬约束）
------------------------------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，加外键只能"建新表 + 拷数据 +
换名"。``recreate="auto"`` 在 SQLite 上等价于 always，在 PostgreSQL 上走原生
``ALTER TABLE ADD CONSTRAINT``（不重建表）。同一份代码覆盖两方言，避免
"本地 SQLite 绿、真实 PG 炸"这类只有上线才发现的分叉。

⚠️ 守卫拆装顺序不可调换（沿用 0040 实测结论）
--------------------------------------------
迁移 0007 装在 ``collection_batches`` 上的触发器
``trg_collection_batches_transition``，正文里查询 ``collection_tasks``。
batch 重建会先把 ``collection_tasks`` 改名为 ``_alembic_tmp_collection_tasks``，
SQLite 在改名那一刻解析该触发器失败，报
``error in trigger trg_collection_batches_transition: no such table: main.collection_tasks``，
整个迁移中止。因此升级时**先** ``drop_collection_state_guards``，三张表重建
完成后再 ``install_collection_state_guards`` 装回。

``robots`` / ``devices`` 上没有触发器（已核：全库触发器里只有上述一条正文
引用了 ``collection_tasks``，没有任何触发器引用 ``robots`` / ``devices``），
重建它们不影响守卫；但仍放在同一段里统一装拆，保证顺序只有一处。

回滚
----
``downgrade`` 是 upgrade 的严格逆操作：先卸守卫、按逆序删三条外键、再装回守卫，
最后删 ``locations`` 表与其索引。
"""

from __future__ import annotations

# ruff: noqa: S608 - 清理/校验语句的表名与列名只来自本模块常量，不接收任何外部输入；
# 与 0040 及 ``collection_guards.py`` 的既有做法一致（那里也是常量拼 DDL）。
import logging

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_collection_state_guards,
    install_collection_state_guards,
)

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

logger = logging.getLogger("ddp.migration.0041")

_ADDRESS_LENGTH = 500
_NOTES_LENGTH = 2000
_KIND_VALUES = "'SITE', 'ROOM', 'LAB'"

# 引用列所在表 -> 外键名；顺序即添加顺序，downgrade 逆序使用。
_LOCATION_FOREIGN_KEYS: tuple[tuple[str, str], ...] = (
    ("robots", "fk_robots_project_location"),
    ("devices", "fk_devices_project_location"),
    ("collection_tasks", "fk_collection_tasks_project_location"),
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


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("address", sa.String(length=_ADDRESS_LENGTH), nullable=True),
        sa.Column("notes", sa.String(length=_NOTES_LENGTH), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_locations"),
        sa.UniqueConstraint("project_id", "id", name="uq_locations_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_locations_project_code"),
        sa.CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_allowed"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        *_audit_constraints("locations"),
    )
    op.create_index("ix_locations_project_kind", "locations", ["project_id", "kind"])
    op.create_index("ix_locations_project_code", "locations", ["project_id", "code"])

    _clear_dangling_location_references()
    _add_location_foreign_keys()


def _clear_dangling_location_references() -> None:
    """清空三张表上悬空/跨项目的 ``location_id``，并逐行留痕。

    ``locations`` 在本迁移刚建、必然为空，所以现存任何非空 ``location_id``
    都无法在本项目内解析——正是 N9 缺陷的后果。不清掉的话 ``ADD CONSTRAINT``
    会在存量行上校验失败，整个升级回滚。

    判定条件与即将添加的外键约束**逐字对应**（同项目内存在该 ID），
    这样"迁移清理掉的行"恰好就是"外键会拒绝的行"，不存在中间地带。

    先 SELECT 拿到受影响行再 UPDATE，避免逐行 UPDATE 的 N+1 迁移耗时；
    留痕用的是 SELECT 阶段的结果，因此每行都会有一条日志。
    """
    connection = op.get_bind()
    total = 0
    for table, _constraint in _LOCATION_FOREIGN_KEYS:
        dangling = (
            f"SELECT t.id, t.location_id FROM {table} AS t "
            "WHERE t.location_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM locations AS l "
            "WHERE l.project_id = t.project_id AND l.id = t.location_id)"
        )
        affected = connection.execute(sa.text(dangling)).fetchall()
        if not affected:
            logger.info("N9：%s 无悬空或跨项目的 location_id，无需清理", table)
            continue
        for row_id, discarded in affected:
            logger.warning(
                "N9：清空 %s 行 %s 的 location_id（原值 %s 在本项目内不存在——"
                "locations 表在 0041 后才存在，此前的引用无法解析，原值不予保留）",
                table,
                row_id,
                discarded,
            )
        connection.execute(
            sa.text(
                f"UPDATE {table} AS t SET location_id = NULL "
                "WHERE t.location_id IS NOT NULL AND NOT EXISTS ("
                "SELECT 1 FROM locations AS l "
                "WHERE l.project_id = t.project_id AND l.id = t.location_id)"
            )
        )
        logger.warning("N9：共清理 %s 的 %d 条悬空/跨项目 location_id", table, len(affected))
        total += len(affected)
    if total:
        logger.warning("N9：三张表合计清理 %d 条悬空/跨项目 location_id", total)


def _add_location_foreign_keys() -> None:
    """给三张表补 ``(project_id, location_id)`` 复合外键。

    移除守卫 → 逐表 batch 重建 → 装回守卫。顺序不可调换：见模块头注释。
    """
    connection = op.get_bind()
    drop_collection_state_guards(connection)
    for table, name in _LOCATION_FOREIGN_KEYS:
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.create_foreign_key(
                name,
                "locations",
                ["project_id", "location_id"],
                ["project_id", "id"],
            )
    install_collection_state_guards(connection)


def _drop_location_foreign_keys() -> None:
    connection = op.get_bind()
    drop_collection_state_guards(connection)
    for table, name in reversed(_LOCATION_FOREIGN_KEYS):
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.drop_constraint(name, type_="foreignkey")
    install_collection_state_guards(connection)


def downgrade() -> None:
    _drop_location_foreign_keys()

    op.drop_index("ix_locations_project_code", table_name="locations")
    op.drop_index("ix_locations_project_kind", table_name="locations")
    op.drop_table("locations")
