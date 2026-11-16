"""AI 预标注建议 ORM 模型。

与 ``annotation_records`` 物理分离：模型输出（``suggested_value`` 及模型身份三列）
与人工最终值（``human_value``）各自成列，二者不会互相覆盖。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

# ``none_as_null=True`` 是必需的，不是可选项：SQLAlchemy 的 ``JSON`` 默认把
# Python ``None`` 序列化成 JSON 字面量 ``null``（在某些方言里落成字符串
# ``'null'``），于是 ``human_value IS NULL`` 判定为假——``decision_consistency``
# 这条 CHECK 会误拒合法的"未确认"行（实测 SQLite 即如此）。
# 本表只有 ``human_value`` 一个可空 JSON 列，且它必须是 SQL NULL。
_JSON_TYPE = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")

# 与领域规则同义（``domain/annotation/ai_suggestion.py`` 的
# ``_require_decision_consistency``）：确认就必须有人工值、决定人与决定时间；
# 未确认时三者必须同时为空。这里是防绕过应用层直写数据库的兜底。
_DECISION_CONSISTENCY_CHECK = (
    "(human_confirmed = false"
    " AND human_value IS NULL AND decided_by IS NULL AND decided_at IS NULL)"
    " OR (human_confirmed = true"
    " AND human_value IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)"
)


class AnnotationAiSuggestion(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_ai_suggestions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    annotation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    label_key: Mapped[str] = mapped_column(String(100), nullable=False)
    suggested_value: Mapped[object] = mapped_column(_JSON_TYPE, nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    human_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    human_value: Mapped[object | None] = mapped_column(_JSON_TYPE, nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_ai_suggestions_project_id_id"),
        # 同一模型版本对同一记录的同一标签只留一条；换模型或换 Prompt 版本
        # 会自然产生新行，从而支持「同一条数据被多个模型建议过」的对比统计。
        UniqueConstraint(
            "project_id",
            "annotation_id",
            "label_key",
            "model_id",
            "model_version",
            "prompt_version",
            name="uq_annotation_ai_suggestions_identity",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotation_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name="fk_annotation_ai_suggestions_project_record",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "decided_by"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_ai_suggestions_project_decided_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint("length(label_key) > 0", name="label_key_non_empty"),
        CheckConstraint("length(model_id) > 0", name="model_id_non_empty"),
        CheckConstraint("length(model_version) > 0", name="model_version_non_empty"),
        CheckConstraint("length(prompt_version) > 0", name="prompt_version_non_empty"),
        CheckConstraint(_DECISION_CONSISTENCY_CHECK, name="decision_consistency"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_ai_suggestions_project_annotation",
            "project_id",
            "annotation_id",
        ),
        # N7「AI 建议 vs 人工修订」按模型版本切分统计的支撑索引。
        Index(
            "ix_annotation_ai_suggestions_project_model",
            "project_id",
            "model_id",
            "model_version",
        ),
    )
