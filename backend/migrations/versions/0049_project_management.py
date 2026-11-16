"""阶段 8 项目管理：为 ``projects`` 补描述、状态与乐观锁版本

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-17

编号说明
--------
本迁移在开发期占用 0046，合并时主线已推进到 0048，故重排为 **0049**，
接在 0048（治理：配额与用量快照）之后。迁移链保持单头：
0040 → 0041 → 0042 → 0043 → 0048 → 0049。

问题（审计结论，不是推测）
--------------------------
``projects``（迁移 0002）只有 ``code`` / ``name`` / 时间戳，没有任何接口能
创建或修改项目——项目只能靠 seed/脚本写入。而 ``project_id`` 是全平台的隔离
基石（ADR-0004：所有业务表带 ``project_id``，所有端点服务端强制项目隔离）。
结果是平台最核心的组织维度既没有 HTTP 入口，也没有"改名/停用"的能力：

- 想改项目名只能在数据库里手写 ``UPDATE``，没有审计、没有并发保护；
- 项目下线只能靠删除，而 ``projects.id`` 被全部业务表以 ``ON DELETE CASCADE``
  引用，删一行等于销毁该项目的全部数据。这在本阶段**不可接受**，
  因此补一个 ``ARCHIVED`` 状态位，删除入口留空。

本迁移只加列与约束，不改动任何存量数据：

1. ``description VARCHAR(1000) NULL``：项目说明，可空（存量项目本来就没有）；
2. ``status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'``：存量项目一律视为活跃，
   语义不变；
3. ``version INTEGER NOT NULL DEFAULT 1``：``PATCH`` 的 ``If-Match`` 乐观锁，
   与场地/设备/数据集的既有口径一致；
4. ``CHECK status IN ('ACTIVE','ARCHIVED')`` 与 ``CHECK version >= 1``：
   应用层已校验，数据库是最后兜底，防止运维脚本写入未知状态；
5. ``(status, code)`` 索引：项目列表按 ``status`` 过滤、按 ``code`` 键集分页。

为什么用 ``batch_alter_table``（SQLite 的硬约束）
------------------------------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，加 CHECK 只能重建表。
``recreate="auto"`` 在 SQLite 上等价于 always，在 PostgreSQL 上走原生
``ALTER TABLE ADD COLUMN / ADD CONSTRAINT``（不重建表），同一份代码覆盖两方言。

重建 ``projects`` 是安全的：SQLite 默认 ``PRAGMA foreign_keys=OFF``，
Alembic 运行迁移时也没有打开它（见 ``migrations/env.py``），
子表的 ``ON DELETE CASCADE`` 外键在重建后仍指向表名 ``projects``。
重建会保留既有 ``uq_projects_code`` 唯一约束——迁移测试对此有断言。

回滚
----
``downgrade`` 是 ``upgrade`` 的严格逆操作：先删索引，再在同一个 batch 里
删两条 CHECK、三列。回滚**不丢业务数据**：三列都是本迁移新增的，
删掉只会失去"描述/状态/版本"，``code`` / ``name`` 与全部子表引用原样保留。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049"
# 合并 0044/0045 后改成 "0045"（见模块文档顶部的警告）。
down_revision = "0048"
branch_labels = None
depends_on = None

_TABLE = "projects"
_INDEX = "ix_projects_status_code"
_STATUS_CHECK = "ck_projects_status_allowed"
_VERSION_CHECK = "ck_projects_version_positive"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.add_column(sa.Column("description", sa.String(length=1000), nullable=True))
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'ACTIVE'"),
            )
        )
        batch.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        # ``op.f()``：名字已按 ORM 的命名约定展开，batch 不要再拼前缀。
        # 不加它会得到 ``ck_projects_ck_projects_status_allowed``，
        # 与 ``create_all`` 建库的约束名不一致（迁移 0043 已实测过该差异）。
        batch.create_check_constraint(
            op.f(_STATUS_CHECK),
            "status IN ('ACTIVE', 'ARCHIVED')",
        )
        batch.create_check_constraint(op.f(_VERSION_CHECK), "version >= 1")
    op.create_index(_INDEX, _TABLE, ["status", "code"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.drop_constraint(op.f(_STATUS_CHECK), type_="check")
        batch.drop_constraint(op.f(_VERSION_CHECK), type_="check")
        batch.drop_column("version")
        batch.drop_column("status")
        batch.drop_column("description")
