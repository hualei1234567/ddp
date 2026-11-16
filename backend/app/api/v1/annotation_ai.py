"""AI 预标注建议 HTTP 路由。

三个端点：
- ``POST /annotation-submissions/{id}/ai-suggestions``：登记一批模型建议；
- ``GET  /annotation-submissions/{id}/ai-suggestions``：按提交查询；
- ``POST /annotation-ai-suggestions/{id}/confirm``：人工确认/修订（带 ``If-Match``）。

**契约缺口（本次不改契约文件）**：``packages/contracts`` 里没有这三条路径，
也没有 ``annotation_ai_suggestions`` 的响应模型。契约侧只有
``docs/architecture/contract-draft/annotation/annotation-submission.v1.schema.json``
定义过嵌套的 ``auto_label{model_id, model_version, prompt_version,
confidence, human_confirmed}``，而生效契约 ``packages/contracts/fragments/schemas.stage3.yaml``
连这个对象都没有。契约补齐需要单独走 ADR/版本流程（见交付报告的缺口说明）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from app.api.v1.annotation_ai_models import (
    AiSuggestionConfirm,
    AiSuggestionOut,
    AiSuggestionsCreate,
)
from app.api.v1.annotation_common import (
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import NewAiSuggestions

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-ai-suggestions"])


@router.post(
    "/annotation-submissions/{submission_id}/ai-suggestions",
    response_model=PageOut[AiSuggestionOut],
    status_code=201,
)
def record_ai_suggestions(
    submission_id: UUID,
    payload: AiSuggestionsCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> PageOut[AiSuggestionOut]:
    suggestions = use_cases.ai_suggestions.record_suggestions(
        scope,
        submission_id,
        NewAiSuggestions(
            suggestions=tuple(item.model_dump() for item in payload.suggestions),
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return PageOut(
        items=[AiSuggestionOut.from_domain(item) for item in suggestions],
        next_cursor=None,
    )


@router.get(
    "/annotation-submissions/{submission_id}/ai-suggestions",
    response_model=PageOut[AiSuggestionOut],
)
def list_ai_suggestions(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> PageOut[AiSuggestionOut]:
    suggestions = use_cases.ai_suggestions.list_for_submission(scope, submission_id)
    return PageOut(
        items=[AiSuggestionOut.from_domain(item) for item in suggestions],
        next_cursor=None,
    )


@router.post(
    "/annotation-ai-suggestions/{suggestion_id}/confirm",
    response_model=AiSuggestionOut,
)
def confirm_ai_suggestion(
    suggestion_id: UUID,
    payload: AiSuggestionConfirm,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> AiSuggestionOut:
    suggestion = use_cases.ai_suggestions.confirm(
        scope,
        suggestion_id,
        expected_version=expected_version,
        human_value=payload.human_value,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AiSuggestionOut.from_domain(suggestion)
