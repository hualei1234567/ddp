"""AI 预标注建议 HTTP 路由。

四个端点：
- ``POST /annotation-submissions/{id}/ai-suggestions``：登记一批模型建议；
- ``GET  /annotation-submissions/{id}/ai-suggestions``：按提交查询；
- ``POST /annotation-ai-suggestions/{id}/confirm``：人工确认/修订（带 ``If-Match``）；
- ``GET  /annotation-submissions/{id}/ai-suggestions/agreement``：按模型版本
  聚合"采纳 / 修订 / 待处理"——N7 反馈闭环缺的输入。

前三个已进契约 1.8.0（``packages/contracts/fragments/paths.stage8.yaml``）；
``agreement`` 目前是运行时独有，属本轮新增，契约待下个版本收录
（``check_contract_parity.py`` 会如实报出这 1 项）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from app.api.v1.annotation_ai_models import (
    AgreementItemOut,
    AgreementOut,
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
    submission = use_cases.submissions.get(scope, submission_id)
    redact_human_value = use_cases.submissions.should_redact_labels(scope, submission)
    return PageOut(
        items=[
            AiSuggestionOut.from_domain(item, redact_human_value=redact_human_value)
            for item in suggestions
        ],
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
    submission = use_cases.submissions.get(scope, submission_id)
    redact_human_value = use_cases.submissions.should_redact_labels(scope, submission)
    return PageOut(
        items=[
            AiSuggestionOut.from_domain(item, redact_human_value=redact_human_value)
            for item in suggestions
        ],
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


@router.get(
    "/annotation-submissions/{submission_id}/ai-suggestions/agreement",
    response_model=AgreementOut,
)
def get_suggestion_agreement(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AgreementOut:
    """按模型版本聚合 AI 建议的人工处置（采纳率）。

    ``acceptance_rate`` 的分母是**已决定**条数，不是总数：把待处理算进分母
    会让"还没人看"拉低采纳率，读起来像"模型不行"。无已决定条目时该字段为
    ``null`` 而非 0——"没有样本"与"采纳率 0"是两件事。
    """
    stats = use_cases.ai_suggestions.agreement(scope, submission_id)
    return AgreementOut(items=[AgreementItemOut.from_stats(item) for item in stats])
