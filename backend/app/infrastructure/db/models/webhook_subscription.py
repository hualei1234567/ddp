"""Webhook 订阅 ORM（阶段 5）。列与迁移 0085 一一对应。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class WebhookSubscriptionModel(Base):
    """一条下游 Webhook 订阅。``secret_hash`` 只存 SHA-256，不存明文。"""

    __tablename__ = "webhook_subscriptions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String(256), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(astext_type=String()), "postgresql"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
