"""episodes.readiness_blockers —— 自动就绪被拒绝的原因可查询

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-17

背景（主链唯一断点）
--------------------
导入链路能真实产出 RawAsset 与 Episode，但 Episode 停在 ``PENDING``：进入
``READY`` 需要提交 Canonical Manifest，而平台此前没有任何组件从已落库事实
生成它（``docs/testing/主链端到端与可追溯性验收-2026-11-17.md`` §6.2 记为
``MISSING``，e2e 用显式 ``actions/ready`` 补位）。

补上生成器之后，新的问题立刻出现：**自动判定失败时原因放在哪**。
"导入成功但 Episode 未就绪"是两种不同的结果，必须能分别回答：

- 导入任务：``SUCCEEDED``（数据已落地）；
- Episode：``PENDING`` + **为什么**（缺机位 / 时间同步未验证 / 多机位缺标定 /
  帧率未知……）。

为什么不用 ``failure_reason``
-----------------------------
``failure_reason`` 已有确定语义并已被三处占用：``mark_failed``（FAILED）、
``reject``（REJECTED）、``invalidate``（INVALIDATED），以及创建时
``success=false`` 的采集失败原因。把"还不能就绪"写进去会造成两个后果：

1. 一条仍在正常流水线里的 Episode 被任何按 ``failure_reason`` 统计失败的面板
   计为失败；
2. 后续该 Episode 真的被驳回时，真正的原因会覆盖掉就绪阻塞原因，两者无法共存。

因此单独一列：``readiness_blockers`` 是**运维事实**（可空可清），
``failure_reason`` 保持业务失败语义不变。

列语义
------
- 形状：``[{"code": "...", "message": "...", "detail": {...}}]``；
- 非空数组 ⟺ 自动就绪被拒绝且 Episode 仍是 ``PENDING``；
- 空数组 + ``PENDING`` ⟺ 尚未尝试自动就绪（迁移前的历史行也是这个形态）；
- ``READY`` 起必须为空（域模型有校验：READY 之后不得残留阻塞项）。

为什么可空为 False 且带 ``'[]'`` 默认
------------------------------------
历史行（本迁移前创建）没有判定记录，语义正是"尚未尝试"，空数组是最贴切的
表示；``server_default`` 让存量行在升级时一次填好，不必写数据迁移。

两条建库路径
------------
``episodes`` 表由 ``0009`` 以显式列建出（不是 ORM ``create()``），但
``tests/e2e`` 等夹具走 ``Base.metadata.create_all`` 建表，那时列已随 ORM 元数据
建好。因此本迁移只做 ``add_column``（``create_all`` 路径不会执行迁移），
不存在"重复列"的分叉——与 0033 给 ``raw_assets`` 补列的情形相同。

回滚
----
``downgrade`` 直接删列，可反复升降级。删列不涉及任何其他对象
（无索引、无约束、无触发器引用本列）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "episodes",
        sa.Column(
            "readiness_blockers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("episodes", "readiness_blockers")
