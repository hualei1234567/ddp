"""Webhook 出站投递队列表（阶段 6 / 蓝图 N5 生态对接落地）。

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-27

背景
----
阶段 5 建了 ``webhook_subscriptions``（订阅）与投递用例
（``app/application/webhook/delivery.py``），但投递用例 ``deliver_event``
在**生产代码里没有任何调用点**——有能力、无接线：订阅能建，但事件永远送不出去。
本迁移补上缺失的中间环节：一张出站投递队列表，让业务事件能**同事务**登记
待投递记录，由专用 Worker 拉取并真实发往下游。

为什么不复用 ``outbox_events``
------------------------------
``outbox_events`` 的 ``dispatch`` 通道是**平台内部**事件投递语义：事件由平台
自己的 Worker 消费（校验、导出、导入……），白名单在
``scripts/check_outbox_events.py`` 的 ``DISPATCH_EVENTS``，并受
``docs/architecture/outbox事件语义界定-v1.0.md`` 约束。把「对外 HTTP 投递」
塞进去会同时污染两件事：

1. 内部投递事件与外部投递事件混在同一张表，Worker 抢占时必须靠 event_type
   区分，任何漏写就会让外部事件被内部 Worker 捞走（或反之）；
2. 外部投递的失败语义（连续失败自动 PAUSED 订阅、重试退避、死信）与内部
   投递（attempts 达上限进 FAILED）并不等价，混用会让"订阅被暂停"这件事
   在内部队列里表现为一条普通死信，运营看不出是订阅熔断还是事件积压。

因此本表是**独立**的对外投递队列：语义单一、边界清楚、可单独归档。

设计要点
--------
- ``(subscription_id, event_type, event_id)`` 唯一：同一事件对同一订阅只登记一次
  （``event_id`` 沿用业务侧事件 id，天然去重，重复登记撞唯一键而非产生第二条）；
- ``status`` 库层 CHECK 枚举：``PENDING``（待投递）/ ``DELIVERED``（已送达）/
  ``FAILED``（死信）；
- ``attempts`` 与 ``available_at`` 支持退避重试；
- ``last_error`` 记录最后失败原因（截断 2000 字符，与 outbox 同口径）；
- 项目外键 CASCADE：项目删除时待投递记录随迁。

双方言（MEM-INC-013）
--------------------
建表 DDL 两侧同构：无 ALTER TYPE、无 DROP CONSTRAINT、无 pg_constraint 查询。

回滚
----
``downgrade`` 删表，是 upgrade 的严格逆操作。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0086"
down_revision = "0085"  # main 实际 head
branch_labels = None
depends_on = None

_TABLE = "webhook_outbox"

_STATUS_ALLOWED_SQL = "('PENDING', 'DELIVERED', 'FAILED')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        # 业务事件 id：与业务侧 outbox 事件同一个 id，保证「同一事件同一订阅」
        # 的登记天然幂等（重复登记撞唯一键，而不是产生第二条待投递）。
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint(
            "subscription_id",
            "event_id",
            name=f"uq_{_TABLE}_subscription_event",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["webhook_subscriptions.id"],
            name=f"fk_{_TABLE}_subscription_id_webhook_subscriptions",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"status IN {_STATUS_ALLOWED_SQL}", name=f"ck_{_TABLE}_status_allowed"),
        sa.CheckConstraint("attempts >= 0", name=f"ck_{_TABLE}_attempts_non_negative"),
        sa.CheckConstraint("length(event_type) > 0", name=f"ck_{_TABLE}_event_type_non_empty"),
    )
    # 抢占路径：按 (status, available_at) 取最早可投递的一条。
    op.create_index(f"ix_{_TABLE}_status_available_at", _TABLE, ["status", "available_at"])
    # 项目维度查询（运营按项目看投递情况）。
    op.create_index(f"ix_{_TABLE}_project_created_at", _TABLE, ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_project_created_at", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_status_available_at", table_name=_TABLE)
    op.drop_table(_TABLE)
