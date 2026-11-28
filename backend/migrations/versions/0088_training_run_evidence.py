"""V61-T3-03：新建 ``training_run_evidence`` 训练证据表（迁移 0088）。

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-28

背景
----
ADR-0036 确立 ``external_training_references`` 是「引用行即 run 记录」的权威
落点，但它只承载引用与状态，**没有** seed / checkpoint_hash / 逐步 metrics
这些训练过程证据。GATE-B（``scripts/train/gate_b_train_smoke.py``）已经用真实
lerobot 训练入口跑通 smoke（产出 evidence.json：dataset_version/obs_shape/
act_shape/steps/seed/checkpoint/reload/sha256），本迁移把这类证据落成正式表，
是对 ADR-0036 的**补充而非推翻**：引用行仍是 run 的权威身份，证据行挂在
引用之下，一个引用可有多条证据（smoke 多次重跑 / smoke + real）。

设计要点
--------
- ``run_kind``：``SMOKE``（GATE-B 类冒烟）/ ``REAL``（正式训练）；
- ``exit_code`` 用 int：``0`` 成功、非 0（含负数，信号杀死）失败，不设枚举
  CHECK——退出码域是开放的，约束死会丢真实失败形态；
- ``config_sha256`` / ``checkpoint_sha256`` 沿用仓库 ``sha256:`` 前缀口径
  （与 ``revision_sha256`` 等一致）；checkpoint 相关列可空：smoke 可能在
  保存 checkpoint 之前失败，空值不强制；
- ``metrics_summary`` 可空：失败 run 没有指标，NULL 表示"没有"而非"零"；
- 外键：项目 CASCADE（随项目清理）、引用 RESTRICT（有证据行时拒绝删引用，
  防静默丢失证据）、数据集版本可空引用。

双方言（MEM-INC-013）
--------------------
- 建表 DDL 两侧同构；JSON 列用 ``JSON().with_variant(JSONB, "postgresql")``
  （同 0086/0087 先例）；本迁移无 ALTER，无需方言分支。

回滚
----
- drop 两个索引；
- PostgreSQL 显式回收本表 CHECK 约束（``drop table`` 本会带走，这里显式
  执行防约束悬挂，同 0087 ``_drop_all`` 口径）；
- ``drop table``（外键/主键随表回收）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0088"
down_revision = "0087"  # main 实际 head
branch_labels = None
depends_on = None

_TABLE = "training_run_evidence"

#: 本表 CHECK 约束名前缀（回滚时按前缀回收）。
_CHECK_PREFIX = "ck_training_run_evidence_"

#: JSON 列：SQLite 落 JSON 文本，PostgreSQL 落 JSONB（同 0086 先例）。
_JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
#: 可空 JSON：None 必须存成 SQL NULL（``col IS NULL`` 判定依赖真 NULL）。
_NULLABLE_JSON_TYPE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)

_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _dialect() -> str:
    return op.get_bind().dialect.name


def _relation(table: str) -> str:
    """PostgreSQL 显式限定 public.（0074 的 schema 陷阱）；SQLite 不加前缀。"""
    if _dialect() == "postgresql":
        return f"public.{table}"
    return table


def _reap_check_constraints(connection: sa.Connection, table: str) -> None:
    """PostgreSQL：显式回收表上全部本迁移 CHECK 约束（0087 ``_drop_all`` 口径）。

    ``DROP TABLE`` 本会带走表上约束；这里先逐条 DROP，防止约束名悬挂在
    依赖对象上，也让回滚在 PG 上可观测、可重入。
    """
    if _dialect() != "postgresql":
        return
    relation = _relation(table)
    rows = connection.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "  # noqa: S608 - 表名/前缀均为模块常量
            f"WHERE conrelid = '{relation}'::regclass AND conname LIKE :prefix"
        ),
        {"prefix": f"{_CHECK_PREFIX}%"},
    ).fetchall()
    for (conname,) in rows:
        op.execute(
            sa.text(f"ALTER TABLE {relation} DROP CONSTRAINT {conname}")  # noqa: S608 - 常量
        )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        # 引用行即 run 记录（ADR-0036）：证据挂在引用之下，一个引用可有多条。
        sa.Column("external_training_reference_id", sa.Uuid(), nullable=False),
        # 可空：证据行允许不绑定具体数据集版本（如环境自检类 smoke）。
        sa.Column("dataset_version_id", sa.Uuid(), nullable=True),
        # SMOKE（GATE-B 冒烟）/ REAL（正式训练）。
        sa.Column("run_kind", sa.String(16), nullable=False),
        sa.Column("device", sa.String(64), nullable=False),
        # 运行时版本留痕（torch/lerobot/驱动等），GATE-B evidence.json 的直接映射。
        sa.Column("runtime_versions", _JSON_TYPE, nullable=False),
        sa.Column("config_sha256", sa.String(71), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        # 可空：失败/被中断的 run 没有完成时间。
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # 可空：失败 run 没有指标；NULL 表示"没有"，不是"零"。
        sa.Column("metrics_summary", _NULLABLE_JSON_TYPE, nullable=True),
        # 可空：checkpoint 引用与哈希；smoke 可能在存 checkpoint 前失败。
        sa.Column("checkpoint_ref", sa.String(1024), nullable=True),
        sa.Column("checkpoint_sha256", sa.String(71), nullable=True),
        # int：0 成功、非 0（含负数，信号杀死）失败；退出码域开放，不设枚举。
        sa.Column("exit_code", sa.Integer(), nullable=False),
        # 可空：失败 run 的 stdout 尾部留痕（有界，不存全量日志）。
        sa.Column("stdout_tail", sa.String(8192), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["external_training_reference_id"],
            ["external_training_references.id"],
            name=f"fk_{_TABLE}_reference_id_external_training_references",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=f"fk_{_TABLE}_dataset_version_id_dataset_versions",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_created_by_users",
        ),
        sa.CheckConstraint("run_kind IN ('SMOKE', 'REAL')", name=f"{_CHECK_PREFIX}run_kind"),
        sa.CheckConstraint("steps >= 1", name=f"{_CHECK_PREFIX}steps_positive"),
        sa.CheckConstraint("seed >= 0", name=f"{_CHECK_PREFIX}seed_non_negative"),
        sa.CheckConstraint(
            _SHA_CHECK.format(column="config_sha256"),
            name=f"{_CHECK_PREFIX}config_sha256_format",
        ),
        sa.CheckConstraint(
            "checkpoint_sha256 IS NULL OR (" + _SHA_CHECK.format(column="checkpoint_sha256") + ")",
            name=f"{_CHECK_PREFIX}checkpoint_sha256_format",
        ),
    )
    # 项目维度分页/审计（列表按 (project_id, created_at) 键集扫描）。
    op.create_index(f"ix_{_TABLE}_project_created_at", _TABLE, ["project_id", "created_at"])
    # 按引用反查证据（一个引用多条证据）。
    op.create_index(f"ix_{_TABLE}_reference_id", _TABLE, ["external_training_reference_id"])


def downgrade() -> None:
    """回滚：收索引、显式回收 CHECK 约束（PG），再删表。"""
    connection = op.get_bind()
    _reap_check_constraints(connection, _TABLE)
    op.drop_index(f"ix_{_TABLE}_reference_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_project_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
