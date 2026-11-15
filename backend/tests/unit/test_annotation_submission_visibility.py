"""提交可见性规则的领域单测。

``submission_visible_to`` 是「他人 DRAFT 不可见」的唯一权威实现：
API 单条读取与仓储列表下推都引用它/它的状态常量。这里直接钉住语义，
避免以后有人各写一份 ``status == "DRAFT"`` 判断导致口径漂移。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation.enums import AnnotationSubmissionStatus
from app.domain.annotation.submission import (
    VISIBLE_DRAFT_STATUS,
    submission_visible_to,
)


def test_draft_is_only_visible_to_its_author() -> None:
    author, other = uuid4(), uuid4()

    assert submission_visible_to(AnnotationSubmissionStatus.DRAFT, author, author)
    assert not submission_visible_to(AnnotationSubmissionStatus.DRAFT, author, other)


@pytest.mark.parametrize(
    "status",
    [
        AnnotationSubmissionStatus.SUBMITTED,
        AnnotationSubmissionStatus.IN_REVIEW,
        AnnotationSubmissionStatus.APPROVED,
        AnnotationSubmissionStatus.REJECTED,
        AnnotationSubmissionStatus.ESCALATED,
        AnnotationSubmissionStatus.ARBITRATING,
        AnnotationSubmissionStatus.RESOLVED_APPROVED,
        AnnotationSubmissionStatus.RESOLVED_REJECTED,
        AnnotationSubmissionStatus.RESOLVED_REWORK,
        AnnotationSubmissionStatus.REWORK_REQUIRED,
        AnnotationSubmissionStatus.WITHDRAWN,
        AnnotationSubmissionStatus.SUPERSEDED,
    ],
)
def test_non_draft_status_is_visible_to_any_project_actor(
    status: AnnotationSubmissionStatus,
) -> None:
    annotator, other = uuid4(), uuid4()

    assert submission_visible_to(status, annotator, other)
    assert submission_visible_to(status, annotator, annotator)


def test_status_accepts_plain_string_as_stored_by_orm() -> None:
    """ORM 读回的是字符串而非枚举；规则必须两种形态都能判定。"""
    annotator, other = uuid4(), uuid4()

    assert submission_visible_to(VISIBLE_DRAFT_STATUS, annotator, annotator)
    assert not submission_visible_to(VISIBLE_DRAFT_STATUS, annotator, other)
    assert submission_visible_to("SUBMITTED", annotator, other)
