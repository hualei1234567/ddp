"""N9 规模化运营：建立项目资源配额与用量快照两张表

Revision ID: 0048
Revises: 0043
Create Date: 2026-11-17

问题（审计结论）
----------------
N9 的"用多少、剩多少、超没超"此前完全没有承载点：``grep quota / 配额 / usage``
在 ``backend/app`` 只命中限流器的注释（那不是项目配额），没有任何表、用例
或端点。结果是运营只能靠手工 SQL 估算，且写操作**无法在事中**因资源耗尽
被拒绝——只能事后发现。

本迁移只建两张表，不改任何既有表：

- ``resource_quotas``：项目级、按资源类型的上限配置（每项目每类型至多一行）；
- ``usage_snapshots``：离线 Worker 产出的最近一次用量测量。

为什么是两个表而不是一个
------------------------
配额是**人工配置**，要审计谁在何时改了多少；用量是**系统测量**，每次全量
重算、只保留最新一份。混在一张表里会导致"Worker 刷新用量时覆盖了人工配置"
或"改配额时把测量时间冲掉"，两者生命周期完全不同。

为什么 ``usage_snapshots`` 用复合主键
-------------------------------------
语义就是"每个项目每种资源只有最新一份"。加自增 id 后"取最新"要写
``DISTINCT ON`` / 窗口函数，还会留下无人清理的历史行。历史曲线属于时序库。

为什么用量允许为 0、但报表允许 ``null``
---------------------------------------
本表只保存**成功测量**的结果，因此 ``used_value`` 非空且非负，``0`` 是
合法真实值；"没有快照"由**缺行**表达，报表据此输出 ``used=null``。这样
"测不到"与"真实为 0"在存储层就已经可区分，不依赖调用方自觉。

项目隔离
--------
两表都以 ``project_id`` 外键指向 ``projects.id``（``ON DELETE CASCADE``）；
配额唯一键是 ``(project_id, resource_kind)``，不同项目的同名资源配额互不
影响。用量快照的复合主键同样以 ``project_id`` 开头。

回滚
----
``downgrade`` 先删 ``usage_snapshots`` 再删 ``resource_quotas``（按外键依赖
逆序），不触碰其他表。两张表都是本迁移新建，回滚不丢失升级前的任何数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0043"
branch_labels = None
depends_on = None

_RESOURCE_KIND_VALUES = (
    "'OBJECT_STORAGE_BYTES', 'EPISODES', 'DATASET_VERSIONS', " "'INGESTION_JOBS', 'INFERENCE_TASKS'"
)
_RESOURCE_KIND_CHECK = f"resource_kind IN ({_RESOURCE_KIND_VALUES})"
_SOURCE_MAX_LENGTH = 64


def upgrade() -> None:
    op.create_table(
        "resource_quotas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_resource_quotas"),
        sa.UniqueConstraint(
            "project_id",
            "resource_kind",
            name="uq_resource_quotas_project_kind",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_resource_quotas_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_resource_quotas_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_resource_quotas_updated_by_users",
        ),
        sa.CheckConstraint(_RESOURCE_KIND_CHECK, name="resource_kind_allowed"),
        sa.CheckConstraint("limit_value >= 0", name="limit_non_negative"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )

    op.create_table(
        "usage_snapshots",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("used_value", sa.BigInteger(), nullable=False),
        sa.Column(
            "measurement_source",
            sa.String(length=_SOURCE_MAX_LENGTH),
            nullable=False,
        ),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "resource_kind", name="pk_usage_snapshots"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_usage_snapshots_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_RESOURCE_KIND_CHECK, name="resource_kind_allowed"),
        sa.CheckConstraint("used_value >= 0", name="used_non_negative"),
    )


def downgrade() -> None:
    # 先删引用方（快照），再删配额；顺序与依赖方向相反。
    op.drop_table("usage_snapshots")
    op.drop_table("resource_quotas")
