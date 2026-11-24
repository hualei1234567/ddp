"""V4.1 运维修复：历史遗留的「无消费者 dispatch 事件」归正为留痕类

Revision ID: 0072
Revises: 0071
Create Date: 2026-11-24

背景（真实事故，2026-11-24 部署验证发现）
----------------------------------------
``outbox_events`` 同时承担「变更留痕」与「集成投递」两种职责。当前
``SqlOutboxRepository.enqueue`` 按 ``status="PENDING" if event.dispatch else "RECORDED"``
正确分流，``scripts/check_outbox_events.py`` 也保证「源码里 ``dispatch=True`` 的
事件必须在 ``DISPATCH_EVENTS`` 白名单里且有消费者」。

但**早期代码**（2026-11-14 前后）缺这两道约束，产生了 23 条「``dispatch=True``
却没有消费者」的历史行。它们 ``attempts`` 恒为 0（没有 Worker 会认领），
在 FR-042 队列健康视图里表现为「积压 9 天」，把「本来就没人要消费」误报成
「消费者坏了」——运维无法从指标区分两者。

本次识别出的历史事件类型
------------------------
- ``drill.event``（5 条）：**当前源码已完全不存在**，纯历史残留，payload 为空；
- ``datasource.registered`` / ``datasource.import.start`` /
  ``datasource.review.created`` / ``datasource.review.decided``（18 条）：
  当前代码仍产生这些事件，但**已明确是留痕类**（``record_change`` 默认
  ``dispatch=False``），不在 ``DISPATCH_EVENTS`` 白名单里。

修复方式
--------
把这 4 + 1 类「当前不是投递类」的事件，历史行的 ``dispatch`` 与 ``status``
**同时**归正为 ``false`` / ``RECORDED``。

为什么两个字段都要改：数据库有约束
``ck_outbox_events_dispatch_status_consistent``，语义是
「``dispatch=false`` ⟺ ``status='RECORDED'``」。只改 status 会被约束拒绝——
这也正是该约束的价值：禁止出现「标记为待投递却写成留痕终态」的自相矛盾行。

**白名单是唯一权威**：迁移里不硬编码一份新的「哪些该修」列表，而是只处理
「``status='PENDING'`` 且 ``event_type NOT IN (当前 DISPATCH_EVENTS 白名单)``」
的行。这样：

- 真正的投递类积压（如 ``dataset.export.requested``）原样保留——那是真待办；
- 将来若某个事件从留痕类升级为投递类，只要进了白名单，历史行不会被误改；
- 迁移与代码的白名单天然一致，不会出现「改了代码忘了改迁移」。

为什么迁移里要内联白名单而不是 import 代码
------------------------------------------
迁移必须能在任意历史版本上执行，不能依赖当时的 ``backend/app`` 代码（未来的
代码可能已重构甚至删掉常量）。因此这里内联一份**当时的**白名单快照，并在
注释里说明「它对应 0072 编写时的 DISPATCH_EVENTS」。这是迁移的通行做法：
迁移是历史事实的刻录，不是活代码。

回滚
----
``downgrade`` 为空操作：回填后无法区分「本应 PENDING 的投递类」与「被归正的
历史行」，强行回滚会把真实待办也变成积压。数据修复类迁移不应回退；需要逆向
时用备份恢复。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

_TABLE = "outbox_events"

# 0072 编写时的 DISPATCH_EVENTS 快照（见 scripts/check_outbox_events.py）。
# 只有这里列出的才是「有消费者的真实待办」，其余 dispatch=true 的历史行都是
# 旧代码遗留，应当归正为留痕类。
_DISPATCH_EVENTS_AT_0072: tuple[str, ...] = (
    "ingestion.raw_asset.validation_requested",
    "dataset.export.requested",
    "datasource.import_requested",
    "datasource.import.succeed",
    "ingestion.job.created",
    "processing.run.requested",
    "quality.run.requested",
)


def upgrade() -> None:
    # 只归正「PENDING 且不属当前投递类」的行；真实待办原样保留。
    #
    # dispatch 与 status 必须一起改：数据库约束
    # ``ck_outbox_events_dispatch_status_consistent`` 要求
    # dispatch=false ⟺ status='RECORDED'，只改 status 会被拒绝。
    #
    # 白名单值是代码常量（非用户输入），但仍逐个绑定参数而不拼进 SQL 文本。
    # alembic 的 op.execute 不支持 expanding bindparam，因此手工生成占位符。
    placeholders = ", ".join(f":event_{index}" for index in range(len(_DISPATCH_EVENTS_AT_0072)))
    # 表名来自模块常量、占位符个数由白名单长度决定、所有值都走参数绑定，
    # 拼接部分不含任何外部输入，因此不存在注入面。
    sql = (
        f"UPDATE {_TABLE} SET dispatch = false, status = 'RECORDED' "  # noqa: S608
        "WHERE status = 'PENDING' AND dispatch = true "
        f"AND event_type NOT IN ({placeholders})"
    )
    statement = sa.text(sql)
    for index, event_type in enumerate(_DISPATCH_EVENTS_AT_0072):
        statement = statement.bindparams(**{f"event_{index}": event_type})
    op.execute(statement)


def downgrade() -> None:
    """不可安全回滚：回填后无法区分真实待办与历史遗留。"""
    # 有意不做任何事。把归正过的行改回 PENDING 会让它们重新变成永不被消费的
    # 积压，反而制造事故。需要逆向时请用备份恢复。
    return
