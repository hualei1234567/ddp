"""AI 预标注建议 HTTP DTO。

请求模型统一 ``extra="forbid"``：客户端不能借机塞入 ``project_id``、
``human_confirmed``、``version`` 等由服务端决定的字段。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.annotation.ai_suggestion import (
    MODEL_ID_MAX_LENGTH,
    MODEL_VERSION_MAX_LENGTH,
    PROMPT_VERSION_MAX_LENGTH,
    AiSuggestion,
)


class AiSuggestionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotation_id: UUID
    label_key: str = Field(min_length=1, max_length=100)
    suggested_value: object
    model_id: str = Field(min_length=1, max_length=MODEL_ID_MAX_LENGTH)
    model_version: str = Field(min_length=1, max_length=MODEL_VERSION_MAX_LENGTH)
    prompt_version: str = Field(min_length=1, max_length=PROMPT_VERSION_MAX_LENGTH)
    confidence: float = Field(ge=0, le=1)
    generated_at: datetime | None = None


class AiSuggestionsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[AiSuggestionItem] = Field(min_length=1)
    # V52-010 可选计量条目：模型侧返回的 token 用量。缺省为空——不传就
    # 与旧请求完全一致（不记账）。校验在应用层统一做，这里只声明形状。
    usage: list[TokenUsageItem] = Field(default_factory=list)


class TokenUsageItem(BaseModel):
    """一次调用的计量条目（可选）。

    ``annotation_id`` 缺省表示计入整批；给出时只计入该记录的建议。
    ``purpose`` / ``occurred_at`` 同样可选（缺省 ``PRE_LABEL`` / 服务端时刻）。
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    purpose: str | None = Field(default=None, description="PRE_LABEL / LLM_JUDGE / EMBED")
    annotation_id: UUID | None = None
    occurred_at: datetime | None = None


class AiSuggestionConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_value: object


class AiSuggestionOut(BaseModel):
    suggestion_id: UUID
    project_id: UUID
    annotation_id: UUID
    label_key: str
    suggested_value: object
    model_id: str
    model_version: str
    prompt_version: str
    confidence: float
    generated_at: datetime
    human_confirmed: bool
    human_value: object | None
    decided_by: UUID | None
    decided_at: datetime | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(
        cls,
        suggestion: AiSuggestion,
        *,
        redact_human_value: bool = False,
    ) -> AiSuggestionOut:
        """构造建议响应；盲审任务的非标注人看不到人工最终值。

        ``suggested_value`` 是模型输出，不属于"他人的标注结果"，保持可见；
        ``human_value`` 是人工决定，开启盲审后不得下发。``human_confirmed``
        只说明"有人决定过"，不暴露决定内容。
        """
        return cls(
            suggestion_id=suggestion.suggestion_id,
            project_id=suggestion.project_id,
            annotation_id=suggestion.annotation_id,
            label_key=suggestion.label_key,
            suggested_value=suggestion.suggested_value,
            model_id=suggestion.model_id,
            model_version=suggestion.model_version,
            prompt_version=suggestion.prompt_version,
            confidence=suggestion.confidence,
            generated_at=suggestion.generated_at,
            human_confirmed=suggestion.human_confirmed,
            human_value=None if redact_human_value else suggestion.human_value,
            decided_by=suggestion.decided_by,
            decided_at=suggestion.decided_at,
            version=suggestion.version,
            created_at=suggestion.created_at,
            updated_at=suggestion.updated_at,
            created_by=suggestion.created_by,
            updated_by=suggestion.updated_by,
        )


class AgreementItemOut(BaseModel):
    """单个模型版本的采纳统计。

    `acceptance_rate` 为 `None` 表示没有任何已决定条目——**不是 0**。
    前端不得把它渲染成 0%。
    """

    model_id: str
    model_version: str
    total: int
    accepted: int
    revised: int
    pending: int
    decided: int
    acceptance_rate: float | None

    @classmethod
    def from_stats(cls, stats: object) -> AgreementItemOut:
        return cls(
            model_id=stats.model_id,  # type: ignore[attr-defined]
            model_version=stats.model_version,  # type: ignore[attr-defined]
            total=stats.total,  # type: ignore[attr-defined]
            accepted=stats.accepted,  # type: ignore[attr-defined]
            revised=stats.revised,  # type: ignore[attr-defined]
            pending=stats.pending,  # type: ignore[attr-defined]
            decided=stats.decided,  # type: ignore[attr-defined]
            acceptance_rate=stats.acceptance_rate,  # type: ignore[attr-defined]
        )


class AgreementOut(BaseModel):
    items: list[AgreementItemOut]
