"""标注状态 → Episode 投影：规则只在这里实现一次。

**为什么必须是一个共享模块**

Episode 的 ``annotation_status`` 是标注流程的**读模型**，有三个推进点：

1. 提交用例的 ``apply_action``（提交/撤回/要求返工）；
2. 复核用例的 ``_apply_review_to_submission``（批准/驳回/升级）；
3. 仲裁用例的 resolve（维持驳回/改判批准）。

三处若各写一份"什么状态映射成 Episode 的什么状态"，必然漂移——而漂移的
后果是硬的：N5 数据集选择只认 ``APPROVED``，投影错一次这批数据就永远进不了
数据集，且从错误现场很难看出是投影写错还是标注没做。

因此规则集中在本模块，三个用例都调 ``project_submission_status``。

**此前这一整层不存在**：``grep annotation_status app/application/annotation/``
只命中样本集里写死的 ``NOT_STARTED``。后果是即使标注全流程批准，Episode 仍
是 ``NOT_STARTED``，N5 会把这些 Episode 全部以 ``ANNOTATION_NOT_APPROVED``
排除——而既有 N5 测试靠手工 ``UPDATE episodes SET annotation_status='APPROVED'``
绕过，把这条断链掩盖了。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from app.application.annotation.ports import EpisodeDirectory
from app.domain.annotation.enums import AnnotationSubmissionStatus
from app.domain.identity.models import ProjectScope

# 提交状态 → Episode 标注状态。**不在表里的状态不投影**（保持原值），
# 因为它们是流程中间态或与 Episode 级结论无关（例如 WITHDRAWN 说明这次
# 提交撤回了，不代表这个 Episode 没有被标注过）。
_SUBMISSION_TO_EPISODE: dict[str, str] = {
    AnnotationSubmissionStatus.DRAFT.value: "IN_PROGRESS",
    AnnotationSubmissionStatus.SUBMITTED.value: "SUBMITTED",
    AnnotationSubmissionStatus.IN_REVIEW.value: "SUBMITTED",
    AnnotationSubmissionStatus.ESCALATED.value: "SUBMITTED",
    AnnotationSubmissionStatus.ARBITRATING.value: "SUBMITTED",
    AnnotationSubmissionStatus.REWORK_REQUIRED.value: "REJECTED",
    AnnotationSubmissionStatus.RESOLVED_REWORK.value: "REJECTED",
    AnnotationSubmissionStatus.APPROVED.value: "APPROVED",
    AnnotationSubmissionStatus.RESOLVED_APPROVED.value: "APPROVED",
    AnnotationSubmissionStatus.REJECTED.value: "REJECTED",
    AnnotationSubmissionStatus.RESOLVED_REJECTED.value: "REJECTED",
}


def project_submission_status(
    episodes: EpisodeDirectory,
    scope: ProjectScope,
    *,
    submission_status: AnnotationSubmissionStatus | str,
    episode_ids: Iterable[UUID],
    now: datetime,
) -> int:
    """把一次提交状态变化投影到它涉及的 Episode 上；返回更新行数。

    调用方负责事务（与本用例的其他写操作同批提交）。``episode_ids`` 通常是
    该提交下全部 ``AnnotationRecord.episode_id`` 的去重集合。
    """
    value = (
        submission_status.value
        if isinstance(submission_status, AnnotationSubmissionStatus)
        else str(submission_status)
    )
    target = _SUBMISSION_TO_EPISODE.get(value)
    if target is None:
        return 0
    unique = tuple(dict.fromkeys(episode_ids))
    if not unique:
        return 0
    return episodes.apply_annotation_status(
        scope,
        episode_ids=unique,
        status=target,
        now=now,
    )


__all__ = ["project_submission_status"]
