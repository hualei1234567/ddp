"""黄金题熔断状态 ORM 模型（V52-008）。

按 ``(project_id, annotator_id)`` 唯一的状态行：持久化
:class:`~app.domain.annotation.honeypot_breaker.HoneypotBreaker` 的
滑窗命中序列与熔断状态，供派单入口消费。领域状态机是纯内存的；本表
是它的持久化投影——``window_history``（``0``/``1`` 字符序列，最长
``WINDOW_SIZE * CONSECUTIVE_WINDOWS`` = 20）忠实保存最近命中序列，
``hits`` / ``total`` 是它的派生聚合列（运维可读 + 库层约束兜底）；
resume 时序列清空。

**为什么必须存序列而不是只存聚合**：滑窗判定按提交顺序分窗，聚合值
（hits/total）丢失顺序无法忠实重放——实测反例：两窗各 80%（真实
PAUSED）与窗1 100% + 窗2 60%（真实 NORMAL）聚合同为 hits=16/total=20。

``state`` 库层 CHECK 与领域 ``BreakerState`` 枚举一致（防绕过应用层
直写脏值）；``hits <= total``、``length(window_history) = total`` 与
领域 ``WindowStat`` 不变式同源。``window_history`` 的二进制字符校验
（SQLite GLOB / PG ``!~``，方言分歧）只在迁移 0083 里声明，ORM 不重复。

项目与审计外键（``projects`` / ``users``）由 :class:`AnnotationAuditMixin`
的列级 ``ForeignKey`` 提供，与同域其他表一致，不在 ``__table_args__``
重复声明。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

# 与领域常量同义（domain/annotation/honeypot_breaker.py）；库层兜底。
_STATE_ALLOWED_SQL = "('NORMAL', 'PAUSED')"

#: 滑窗容量（窗口大小 × 连续窗口数）；与领域默认口径一致。
_WINDOW_CAPACITY = 20


class AnnotationHoneypotState(Base, AnnotationAuditMixin):
    """一个标注员在一个项目的黄金题熔断状态行。"""

    __tablename__ = "annotation_honeypot_state"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    annotator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    # 派生聚合列：命中数 / 样本数（与 window_history 严格一致）。
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 滑窗忠实记录：'0' miss / '1' hit，按提交顺序，最长 20。
    window_history: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL")
    paused_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "annotator_id",
            name="uq_annotation_honeypot_state_project_annotator",
        ),
        CheckConstraint(
            f"state IN {_STATE_ALLOWED_SQL}",
            name="ck_annotation_honeypot_state_state_allowed",
        ),
        CheckConstraint("hits >= 0", name="ck_annotation_honeypot_state_hits_non_negative"),
        CheckConstraint("total >= 0", name="ck_annotation_honeypot_state_total_non_negative"),
        CheckConstraint(
            f"total <= {_WINDOW_CAPACITY}",
            name="ck_annotation_honeypot_state_total_capacity",
        ),
        CheckConstraint("hits <= total", name="ck_annotation_honeypot_state_hits_lte_total"),
        CheckConstraint(
            "length(window_history) = total",
            name="ck_annotation_honeypot_state_window_history_matches_total",
        ),
        CheckConstraint(
            "paused_reason IS NULL OR length(paused_reason) > 0",
            name="ck_annotation_honeypot_state_paused_reason_non_empty",
        ),
        # 运维视角："本项目有哪些人被熔断"。
        Index("ix_annotation_honeypot_state_project_state", "project_id", "state"),
    )


__all__ = ["AnnotationHoneypotState"]
