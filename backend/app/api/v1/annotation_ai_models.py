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
    def from_domain(cls, suggestion: AiSuggestion) -> AiSuggestionOut:
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
            human_value=suggestion.human_value,
            decided_by=suggestion.decided_by,
            decided_at=suggestion.decided_at,
            version=suggestion.version,
            created_at=suggestion.created_at,
            updated_at=suggestion.updated_at,
            created_by=suggestion.created_by,
            updated_by=suggestion.updated_by,
        )
