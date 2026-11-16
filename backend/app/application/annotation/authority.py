"""标注域「审阅权限」判定：单一权威实现。

为什么单独成文件：同一条规则在提交返工（``submissions._assert_can_require_rework``）
与 AI 建议人工确认（``ai_suggestions.confirm``）两处都要用。规则只有一份——
"项目负责人 / 投过 REJECT 的复核人 / 本案仲裁人"具备审阅权限，
标注人本人不具备"审阅者"身份（他可以做标注侧动作）。

把事实收集（查成员角色、复核结论、仲裁人名单）与规则判定分开：
:class:`ReviewAuthority` 只承载布尔事实，:func:`has_review_authority` 是纯函数，
便于单测覆盖全部组合，也避免两个调用点各自拼装条件而漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.application.annotation.ports import (
    AnnotationArbitrationRepository,
    AnnotationReviewRepository,
    MemberDirectory,
)
from app.domain.annotation.submission import AnnotationSubmission
from app.domain.identity.models import ProjectRole, ProjectScope

# 复核结论中代表"否定判断"的取值；只有投过 REJECT 的复核人才被视为具备
# 审阅权限——APPROVE 的一方已经推动流程前进，不需要额外处置权。
REJECT_DECISION = "REJECT"


@dataclass(frozen=True, slots=True)
class ReviewAuthority:
    """调用者在某提交上的角色事实。"""

    actor_id: UUID
    annotator_id: UUID
    is_annotator: bool
    is_project_owner: bool
    is_rejecting_reviewer: bool
    is_arbiter: bool

    @property
    def has_review_authority(self) -> bool:
        """是否具备审阅者身份（不含标注人自身的标注侧权利）。"""
        return self.is_project_owner or self.is_rejecting_reviewer or self.is_arbiter

    @property
    def is_submission_owner(self) -> bool:
        """调用者是否就是该提交的标注人。"""
        return self.is_annotator


def has_review_authority(authority: ReviewAuthority) -> bool:
    """纯函数形式，供只拿到事实、不持有仓储的调用方使用。"""
    return authority.has_review_authority


def resolve_review_authority(
    scope: ProjectScope,
    submission: AnnotationSubmission,
    *,
    reviews: AnnotationReviewRepository,
    arbitrations: AnnotationArbitrationRepository,
    members: MemberDirectory,
) -> ReviewAuthority:
    """收集判定所需事实。

    查询顺序保持短路（负责人 → 复核 → 仲裁），与改造前
    ``submissions._assert_can_require_rework`` 的执行路径一致：已是负责人时
    不再多查复核/仲裁两张表，既省往返也不改变既有失败点顺序。
    """
    is_annotator = submission.annotator_id == scope.actor_id
    is_owner = members.user_role(scope, scope.actor_id) == ProjectRole.OWNER.value
    is_rejecting_reviewer = False
    is_arbiter = False
    if not is_owner:
        is_rejecting_reviewer = any(
            review.reviewer_id == scope.actor_id
            and getattr(review.decision, "value", review.decision) == REJECT_DECISION
            for review in reviews.list_for_submission(scope, submission.submission_id)
        )
    if not is_owner and not is_rejecting_reviewer:
        is_arbiter = any(
            scope.actor_id in arbitration.arbiter_ids
            for arbitration in arbitrations.list_for_submission(scope, submission.submission_id)
        )
    return ReviewAuthority(
        actor_id=scope.actor_id,
        annotator_id=submission.annotator_id,
        is_annotator=is_annotator,
        is_project_owner=is_owner,
        is_rejecting_reviewer=is_rejecting_reviewer,
        is_arbiter=is_arbiter,
    )


__all__ = [
    "REJECT_DECISION",
    "ReviewAuthority",
    "has_review_authority",
    "resolve_review_authority",
]
