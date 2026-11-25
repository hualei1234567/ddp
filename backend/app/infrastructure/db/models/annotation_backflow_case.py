"""回流案例 ORM（V52-030，蓝图 N5）。

**不套 ``AnnotationAuditMixin``**：写入者是评测平台的机器回调，没有人类
``created_by`` actor；口径同 ``edge_agents``（人类操作另走审计）。列与迁移
``0082_backflow_cases`` 一一对应。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class AnnotationBackflowCase(Base):
    """一条外部评测失败案例（幂等键 ``source`` + ``case_ref``）。"""

    __tablename__ = "annotation_backflow_cases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    case_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    suggested_priority: Mapped[str] = mapped_column(String(2), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(astext_type=String()), "postgresql"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("annotation_tasks.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
