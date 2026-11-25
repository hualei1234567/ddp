"""Token 成本台账 ORM 模型（V52-010）。

计量事实表：一次 LLM 调用一行。与 ``annotation_ai_suggestions`` 共享同一套
模型身份语义（``model_id`` / ``model_version`` / ``prompt_version``），成本
聚合能对回「哪个模型版本、哪套 Prompt」。

金额 ``cost_estimate`` 用 ``Numeric(14, 4)``（见迁移 0081）；``None`` 表示
"未配置价格"，不与 ``0``（真免费）混用——领域层 ``token_usage.py`` 定义
同一语义，库层 CHECK 只是防绕过应用层的兜底。

**为什么独立建表而不是把 usage 塞进 suggestion**：预标注只是一类用途
（``PRE_LABEL``）；judge 评审、Embedding 调用不产生建议行，成本无处安放。
台账是调用侧的计量事实，与建议（产出侧）生命周期不同：重跑建议会清理旧行，
计量必须保留（否则 ROI 历史失真）。

项目与审计外键（``projects`` / ``users``）由 :class:`AnnotationAuditMixin`
的列级 ``ForeignKey`` 提供，与同域其他表一致，不在 ``__table_args__`` 重复声明。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

# 与领域常量同义（domain/annotation/token_usage.py）；库层兜底防绕过应用层。
_PURPOSE_ALLOWED_SQL = "('PRE_LABEL', 'LLM_JUDGE', 'EMBED')"

# 与 AiSuggestion 的长度上限一致（同一套模型身份语义，不另设上限）。
_MODEL_ID_MAX_LENGTH = 128
_MODEL_VERSION_MAX_LENGTH = 64
_PROMPT_VERSION_MAX_LENGTH = 64


class AiTokenUsage(Base, AnnotationAuditMixin):
    """一次 LLM 调用的计量行。"""

    __tablename__ = "ai_token_usage"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    model_id: Mapped[str] = mapped_column(String(_MODEL_ID_MAX_LENGTH), nullable=False)
    model_version: Mapped[str] = mapped_column(String(_MODEL_VERSION_MAX_LENGTH), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(
        String(_PROMPT_VERSION_MAX_LENGTH), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # 金额：Numeric 承载，None 表示未配置价格（不是 0）。SQLite 把 NUMERIC
    # 当浮点存，4 位小数在双精度安全范围内，读写往返不失真。
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    annotation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_ai_token_usage_project_id_id"),
        CheckConstraint(
            f"purpose IN {_PURPOSE_ALLOWED_SQL}",
            name="ck_ai_token_usage_purpose_allowed",
        ),
        CheckConstraint("input_tokens >= 0", name="ck_ai_token_usage_input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_ai_token_usage_output_tokens_non_negative"),
        CheckConstraint("cost_estimate >= 0", name="ck_ai_token_usage_cost_non_negative"),
        CheckConstraint("length(model_id) > 0", name="ck_ai_token_usage_model_id_non_empty"),
        CheckConstraint(
            "length(model_version) > 0", name="ck_ai_token_usage_model_version_non_empty"
        ),
        CheckConstraint(
            "prompt_version IS NULL OR length(prompt_version) > 0",
            name="ck_ai_token_usage_prompt_version_non_empty",
        ),
        # 时间窗聚合（"本月花了多少"）与成本归属聚合（按模型/用途切分）各一条。
        Index("ix_ai_token_usage_project_occurred", "project_id", "occurred_at"),
        Index("ix_ai_token_usage_project_model_purpose", "project_id", "model_id", "purpose"),
    )


__all__ = ["AiTokenUsage"]
