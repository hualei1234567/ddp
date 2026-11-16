"""N8: collection asset lifecycle guards (DRAFT -> ACTIVE -> ARCHIVED)

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-16

问题（审计结论，不是推测）
--------------------------
迁移 0040 建了四张素材表（``collection_plans`` / ``collection_sops`` /
``scenarios`` / ``action_specs``），``status`` 列有 CHECK 约束限定取值
（``DRAFT`` / ``ACTIVE`` / ``ARCHIVED``），但**没有任何机制限制迁移路径**。
在 0040 之后、N8 之前，素材只有 create/list/get，状态恒为 ``DRAFT``，
所以这个缺口看不出来；N8 补上 ``activate`` / ``archive`` 端点后，它立刻变成
真实风险：

- 应用层状态机在 ``app/domain/collection/asset_state_machines.py``，是权威实现；
- 但任何绕过应用层的写入（运维脚本、别的服务、手工 SQL）都能把
  ``ARCHIVED`` 改回 ``DRAFT``，"归档是终态"这句话就只剩注释。

本迁移补的是**数据库兜底**，与任务/批次（迁移 0006/0007）的既有做法一致：
初始化必须 ``DRAFT``，状态变更必须落在 ``asset_transition_pairs()`` 白名单内。

为什么不加"禁删"触发器
----------------------
任务/批次的守卫里有 ``trg_collection_*_no_delete``——因为那些是执行记录，
删掉就丢失事实。素材不同：``DRAFT`` 素材允许硬删除（N8 的 DELETE 端点），
"被任务引用就不能删"由 0040 建的 ``ON DELETE RESTRICT`` 复合外键保证。
再加一条一律禁删的触发器会把合法的删除路径也堵死。

⚠️ SQLite 的硬约束：触发器正文引用 ``OLD.status`` / ``NEW.status``，
表被重建（``batch_alter_table``）时 SQLite 会在改名那一刻解析触发器而报错
（0040 已实测：``error in trigger ...: no such table``）。本迁移不重建任何表，
只建触发器，因此不需要 ``drop_collection_state_guards`` 的整套卸载；
但 ``downgrade`` 仍先删触发器再动表，保证任何后续重建（未来迁移）不会踩同一个坑。

回滚
----
``downgrade`` 删掉八个触发器（四表 × 初始状态 + 迁移）与 PG 函数，
**不改动任何数据**：本迁移只加约束，没有回填。回滚后应用层状态机仍然生效，
只是失去数据库兜底——与 0040 之后的状态完全一致。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_asset_state_guards,
    install_asset_state_guards,
)

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """安装素材四表的初始状态与迁移兜底触发器。

    复用 ``collection_guards`` 的同一份 DDL：迁移后的库与 ``create_all`` 建的
    测试库行为必须一致（否则"测试绿、真实库不拦"这类分叉只有上线才发现）。
    """
    install_asset_state_guards(op.get_bind())


def downgrade() -> None:
    drop_asset_state_guards(op.get_bind())
