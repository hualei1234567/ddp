"""Webhook 出站投递队列 ORM（阶段 6）。列与迁移 0086 一一对应。

与 ``outbox_events`` 的分工：本表只管**对外** HTTP 投递（发给下游订阅方），
``outbox_events`` 管平台**内部**事件投递。两者语义不同、生命周期不同，
刻意分表，见迁移 0086 的说明。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class WebhookOutboxModel(Base):
    """一条待投递（或已投递/死信）的对外 Webhook 事件。"""

    __tablename__ = "webhook_outbox"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 业务事件 id（与业务侧 outbox 事件同 id）：保证「同一事件同一订阅」
    #: 只登记一次，重复登记撞唯一键而不是产生第二条待投递。
    event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(astext_type=String()), "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
