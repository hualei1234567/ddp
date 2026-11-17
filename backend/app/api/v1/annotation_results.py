"""标注提交、复核与仲裁 HTTP 路由。

提交动作端点只开放标注人可执行的 ``submit`` / ``withdraw``；``approve`` 等
裁决动作只能经复核或仲裁资源执行，避免绕过职责分离。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.annotation_common import (
    ActionName,
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.annotation_result_models import (
    PUBLIC_SUBMISSION_ACTIONS,
    AnnotationArbitrationActionRequest,
    AnnotationArbitrationCreate,
    AnnotationArbitrationOut,
    AnnotationArbitrationVoteRequest,
    AnnotationRecordOut,
    AnnotationReviewActionRequest,
    AnnotationReviewCreate,
    AnnotationReviewOut,
    AnnotationSubmissionActionRequest,
    AnnotationSubmissionArbitrationCreate,
    AnnotationSubmissionCreate,
    AnnotationSubmissionOut,
    AnnotationSubmissionReviewCreate,
)
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import (
    NewAnnotationArbitration,
    NewAnnotationReview,
    NewAnnotationSubmission,
)
from app.core.errors import ForbiddenError, ValidationFailedError
from app.domain.annotation.submission import submission_visible_to

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-submissions"])


@router.post(
    "/annotation-submissions",
    response_model=AnnotationSubmissionOut,
    status_code=201,
)
def create_submission(
    payload: AnnotationSubmissionCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationSubmissionOut:
    submission = use_cases.submissions.create_draft(
        scope,
        NewAnnotationSubmission(
            assignment_id=payload.assignment_id,
            records=tuple(payload.records),
            data_source=payload.data_source,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationSubmissionOut.from_domain(submission)


@router.get(
    "/annotation-submissions",
    response_model=PageOut[AnnotationSubmissionOut],
)
def list_submissions(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    task_id: UUID,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationSubmissionOut]:
    # 可见性由用例/仓储下推到查询；这里不再做内存过滤，否则他人草稿会
    # 占掉分页名额，导致页内条数不足却仍有下一页。
    items = use_cases.submissions.list_for_task(
        scope,
        task_id=task_id,
        statuses=(status,) if status is not None else None,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.submission_id)
    return PageOut(
        items=[AnnotationSubmissionOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/annotation-submissions/{submission_id}",
    response_model=AnnotationSubmissionOut,
)
def get_submission(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationSubmissionOut:
    submission = use_cases.submissions.get(scope, submission_id)
    _assert_not_foreign_draft(scope.actor_id, submission.annotator_id, submission.status)
    return AnnotationSubmissionOut.from_domain(submission)


@router.get(
    "/annotation-submissions/{submission_id}/records",
    response_model=PageOut[AnnotationRecordOut],
)
def list_submission_records(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> PageOut[AnnotationRecordOut]:
    submission = use_cases.submissions.get(scope, submission_id)
    _assert_not_foreign_draft(scope.actor_id, submission.annotator_id, submission.status)
    return PageOut(
        items=[AnnotationRecordOut.from_domain(item) for item in submission.records],
        next_cursor=None,
    )


@router.post(
    "/annotation-submissions/{submission_id}/actions/{action}",
    response_model=AnnotationSubmissionOut,
)
def act_on_submission(
    submission_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationSubmissionActionRequest | None = None,
) -> AnnotationSubmissionOut:
    if action not in PUBLIC_SUBMISSION_ACTIONS:
        raise ValidationFailedError("提交资源不支持该动作，请使用复核或仲裁端点")
    submission = use_cases.submissions.apply_action(
        scope,
        submission_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationSubmissionOut.from_domain(submission)


@router.get(
    "/annotation-submissions/{submission_id}/reviews",
    response_model=PageOut[AnnotationReviewOut],
)
def list_submission_reviews(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> PageOut[AnnotationReviewOut]:
    reviews = use_cases.reviews.list_for_submission(scope, submission_id)
    return PageOut(
        items=[AnnotationReviewOut.from_domain(item) for item in reviews],
        next_cursor=None,
    )


@router.post(
    "/annotation-submissions/{submission_id}/reviews",
    response_model=AnnotationReviewOut,
    status_code=201,
)
def create_submission_review(
    submission_id: UUID,
    payload: AnnotationSubmissionReviewCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationReviewOut:
    review = use_cases.reviews.create(
        scope,
        NewAnnotationReview(submission_id=submission_id, reviewer_id=payload.reviewer_id),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationReviewOut.from_domain(review)


@router.post(
    "/annotation-reviews",
    response_model=AnnotationReviewOut,
    status_code=201,
)
def create_review(
    payload: AnnotationReviewCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationReviewOut:
    review = use_cases.reviews.create(
        scope,
        NewAnnotationReview(
            submission_id=payload.submission_id,
            reviewer_id=payload.reviewer_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationReviewOut.from_domain(review)


@router.get("/annotation-reviews/{review_id}", response_model=AnnotationReviewOut)
def get_review(
    review_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationReviewOut:
    return AnnotationReviewOut.from_domain(use_cases.reviews.get(scope, review_id))


@router.post(
    "/annotation-reviews/{review_id}/actions/{action}",
    response_model=AnnotationReviewOut,
)
def act_on_review(
    review_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationReviewActionRequest | None = None,
) -> AnnotationReviewOut:
    review = use_cases.reviews.apply_action(
        scope,
        review_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        issues=payload.issues if payload is not None else None,
        checklist=payload.checklist if payload is not None else None,
        review_duration_sec=payload.review_duration_sec if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationReviewOut.from_domain(review)


@router.get(
    "/annotation-submissions/{submission_id}/arbitrations",
    response_model=PageOut[AnnotationArbitrationOut],
)
def list_submission_arbitrations(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> PageOut[AnnotationArbitrationOut]:
    arbitrations = use_cases.arbitrations.list_for_submission(scope, submission_id)
    return PageOut(
        items=[AnnotationArbitrationOut.from_domain(item) for item in arbitrations],
        next_cursor=None,
    )


@router.post(
    "/annotation-submissions/{submission_id}/arbitrations",
    response_model=AnnotationArbitrationOut,
    status_code=201,
)
def create_submission_arbitration(
    submission_id: UUID,
    payload: AnnotationSubmissionArbitrationCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationArbitrationOut:
    arbitration = use_cases.arbitrations.create(
        scope,
        NewAnnotationArbitration(
            submission_id=submission_id,
            arbiter_ids=tuple(payload.arbiter_ids),
            quorum=payload.quorum,
            trigger=payload.trigger,
            review_id=payload.review_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationArbitrationOut.from_domain(arbitration)


@router.post(
    "/annotation-arbitrations",
    response_model=AnnotationArbitrationOut,
    status_code=201,
)
def create_arbitration(
    payload: AnnotationArbitrationCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationArbitrationOut:
    arbitration = use_cases.arbitrations.create(
        scope,
        NewAnnotationArbitration(
            submission_id=payload.submission_id,
            arbiter_ids=tuple(payload.arbiter_ids),
            quorum=payload.quorum,
            trigger=payload.trigger,
            review_id=payload.review_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationArbitrationOut.from_domain(arbitration)


@router.get(
    "/annotation-arbitrations/{arbitration_id}",
    response_model=AnnotationArbitrationOut,
)
def get_arbitration(
    arbitration_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationArbitrationOut:
    return AnnotationArbitrationOut.from_domain(use_cases.arbitrations.get(scope, arbitration_id))


@router.post(
    "/annotation-arbitrations/{arbitration_id}/actions/{action}",
    response_model=AnnotationArbitrationOut,
)
def act_on_arbitration(
    arbitration_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationArbitrationActionRequest | None = None,
) -> AnnotationArbitrationOut:
    arbitration = use_cases.arbitrations.apply_action(
        scope,
        arbitration_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationArbitrationOut.from_domain(arbitration)


@router.post(
    "/annotation-arbitrations/{arbitration_id}/votes",
    response_model=AnnotationArbitrationOut,
)
def vote_on_arbitration(
    arbitration_id: UUID,
    payload: AnnotationArbitrationVoteRequest,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
) -> AnnotationArbitrationOut:
    arbitration = use_cases.arbitrations.vote(
        scope,
        arbitration_id,
        decision=payload.decision,
        expected_version=expected_version,
        reason=payload.reason,
        request_id=request_id(request),
    )
    return AnnotationArbitrationOut.from_domain(arbitration)


def _assert_not_foreign_draft(actor_id: UUID, annotator_id: UUID, status: object) -> None:
    """单条读取的可见性判定。

    规则唯一来源是领域层 :func:`submission_visible_to`；这里只负责把"不可见"
    翻译成 HTTP 403。此前本函数内联了 ``status == "DRAFT"`` 判断，与领域层
    和仓储下推的 ``VISIBLE_DRAFT_STATUS`` 构成三份并行实现——状态词汇一旦
    变化必然漏改一处，因此改为委托。
    """
    if not submission_visible_to(status, annotator_id, actor_id):
        raise ForbiddenError("无权查看他人的标注草稿")
