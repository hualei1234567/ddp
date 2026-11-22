"""质检结论驱动路由（V31-032）：领域映射表。

断言的是**判定表**本身，不是应用层编排：新增结论 / 对象类型时，这里必须
先红，才能提醒"下游动作还没定义"。
"""

from __future__ import annotations

import pytest
from app.domain.annotation.enums import AnnotationSubmissionAction
from app.domain.quality.enums import QualityRunLifecycle, QualityRunStatus, QualitySubjectType
from app.domain.quality.routing import decide_route


def test_failed_conclusion_routes_to_require_rework() -> None:
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=QualityRunStatus.FAILED,
        subject_type=QualitySubjectType.EPISODE,
    )

    assert decision.actions == (
        AnnotationSubmissionAction.SYSTEM_REVIEW_START,
        AnnotationSubmissionAction.REJECT,
        AnnotationSubmissionAction.REQUIRE_REWORK,
    )
    assert decision.should_route is True


def test_passed_conclusion_routes_to_system_review_start() -> None:
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=QualityRunStatus.PASSED,
        subject_type=QualitySubjectType.EPISODE,
    )

    assert decision.actions == (AnnotationSubmissionAction.SYSTEM_REVIEW_START,)


def test_incomplete_conclusion_routes_nowhere() -> None:
    """``INCOMPLETE`` 是"没判出结论"，推进任何状态都是伪造。"""
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=QualityRunStatus.INCOMPLETE,
        subject_type=QualitySubjectType.EPISODE,
    )

    assert decision.actions == ()
    assert decision.should_route is False
    assert "INCOMPLETE" in decision.reason


@pytest.mark.parametrize(
    "lifecycle",
    [
        QualityRunLifecycle.QUEUED,
        QualityRunLifecycle.RUNNING,
        QualityRunLifecycle.FAILED,
        QualityRunLifecycle.RETRYING,
        QualityRunLifecycle.CANCELLED,
    ],
)
def test_unfinished_lifecycle_never_routes(lifecycle: QualityRunLifecycle) -> None:
    """未到 ``SUCCEEDED`` 的运行没有可信结论，即使传了 status 也不路由。"""
    decision = decide_route(
        lifecycle=lifecycle,
        status=QualityRunStatus.FAILED,
        subject_type=QualitySubjectType.EPISODE,
    )

    assert decision.actions == ()
    assert lifecycle.value in decision.reason


def test_datasource_import_does_not_route_to_annotation() -> None:
    """导入质检的结论决定是否物化 Episode，与标注提交无关。"""
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=QualityRunStatus.FAILED,
        subject_type=QualitySubjectType.DATASOURCE_IMPORT,
    )

    assert decision.actions == ()
    assert "DATASOURCE_IMPORT" in decision.reason


def test_dataset_version_does_not_route_to_annotation() -> None:
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=QualityRunStatus.PASSED,
        subject_type=QualitySubjectType.DATASET_VERSION,
    )

    assert decision.actions == ()


def test_succeeded_without_status_is_rejected_not_guessed() -> None:
    """领域与数据库都保证 ``SUCCEEDED`` 必有 status；缺失说明被绕过写入。"""
    decision = decide_route(
        lifecycle=QualityRunLifecycle.SUCCEEDED,
        status=None,
        subject_type=QualitySubjectType.EPISODE,
    )

    assert decision.actions == ()
    assert "缺少结论" in decision.reason


def test_accepts_string_values_for_persistence_roundtrip() -> None:
    """仓储读回的是字符串；映射表必须直接接受，不需要调用方先转枚举。"""
    decision = decide_route(
        lifecycle="SUCCEEDED",
        status="FAILED",
        subject_type="EPISODE",
    )

    assert decision.actions[-1] is AnnotationSubmissionAction.REQUIRE_REWORK
