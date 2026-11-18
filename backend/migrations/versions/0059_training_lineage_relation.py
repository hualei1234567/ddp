"""N5-005：``data_lineage_edges.relation`` 增加 ``TRAINED_ON``

Revision ID: 0059
Revises: 0057
Create Date: 2026-11-18

为什么需要这次迁移
------------------
ADR-0036 冻结了 ``DatasetVersion --TRAINED_ON--> TrainingRun`` 这条血缘边，
用于回答"这个数据集版本被哪次训练使用"。``LineageNodeType.TRAINING_RUN``
早在 0009 就已存在，但 ``relation`` 只能取 0009 列出的 10 个值，且由
``ck_data_lineage_edges_relation_allowed`` 这条 CHECK 约束兜底——应用层枚举
改了而数据库没改，写边会在提交时被数据库拒绝。本迁移只放开这一个取值。

口径
----
- 只扩 CHECK，**不改列、不改数据**：存量边（含 0009 以来的全部关系）原样保留；
- 约束名沿用 ORM 命名约定 ``ck_data_lineage_edges_relation_allowed``，与
  ``Base.metadata.create_all`` 建出的名字一致，避免测试库与迁移库两套约束名；
- SQLite 不支持 ``ALTER ... ADD/DROP CONSTRAINT``，走 ``batch_alter_table``
  重建表；PostgreSQL 走原生 ALTER。同一份代码覆盖两方言；
- SQLite 重建表会**连带删除**该表上的触发器（``trg_data_lineage_edges_*``：
  禁改、禁删、禁自环、SUPERSEDES 线性）。因此沿用 0042 的"先卸守卫、重建、
  再装回"顺序，否则血缘"只追加"的数据库兜底会被本迁移静默弄丢。

回滚
----
``downgrade`` 把 CHECK 收回 10 个旧值。血缘边只追加、不删除：若回滚前已经
产生 ``TRAINED_ON`` 边，恢复旧约束必然失败；此时**显式拒绝回滚并给出明确
错误**，而不是静默删除历史血缘。没有任何 ``TRAINED_ON`` 边的库可正常往返。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.episode_guards import (
    drop_episode_state_guards,
    install_episode_state_guards,
)

revision = "0059"
# 当前主线迁移头是 0057；0058 由并行批次预留，尚未合入。这里直接接 0057，
# 保证本分支单头可升级；合并时若 0058 先落地，再由集成方重排为 0059 之后。
down_revision = "0057"
branch_labels = None
depends_on = None

_TABLE = "data_lineage_edges"
_CHECK = "ck_data_lineage_edges_relation_allowed"
_RELATIONS_WITHOUT_TRAINING = (
    "'UPLOADED_BY', 'PRODUCED_BY', 'USED_AS_INPUT', 'PRODUCED', 'SEGMENTED_INTO', "
    "'SUPERSEDES', 'INVALIDATES', 'EVALUATED_BY', 'ANNOTATED_BY', 'INCLUDED_IN'"
)
_RELATIONS_WITH_TRAINING = _RELATIONS_WITHOUT_TRAINING + ", 'TRAINED_ON'"


def _relation_check(values: str) -> str:
    return f"relation IN ({values})"


def upgrade() -> None:
    bind = op.get_bind()
    # 守卫正文引用 ``data_lineage_edges``；SQLite 重建前必须先卸载，重建后再装回。
    drop_episode_state_guards(bind)
    # ``op.f()``：名字已按 ORM 命名约定展开，batch 不要再拼 ``ck_<table>_`` 前缀。
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.drop_constraint(op.f(_CHECK), type_="check")
        batch.create_check_constraint(op.f(_CHECK), _relation_check(_RELATIONS_WITH_TRAINING))
    install_episode_state_guards(bind)


def downgrade() -> None:
    trained_on_edges = (
        op.get_bind()
        .execute(
            sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE relation = 'TRAINED_ON'")  # noqa: S608
        )
        .scalar_one()
    )
    if trained_on_edges:
        raise RuntimeError(
            f"回滚 0059 被拒绝：{_TABLE} 中已有 {trained_on_edges} 条 TRAINED_ON 血缘边。"
            "血缘只追加、不自动删除；请先确认并导出这些边，再决定是否人工处置。"
        )
    bind = op.get_bind()
    drop_episode_state_guards(bind)
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        batch.drop_constraint(op.f(_CHECK), type_="check")
        batch.create_check_constraint(op.f(_CHECK), _relation_check(_RELATIONS_WITHOUT_TRAINING))
    install_episode_state_guards(bind)
