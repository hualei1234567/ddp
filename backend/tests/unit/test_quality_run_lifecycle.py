"""质量运行的生命周期（V31-030）：请求状态与结论是两件事。

现有 ``QualityRun`` 只表达"已经算完的结论"（PASSED/FAILED/INCOMPLETE）。
异步质检还需要表达"排队中 / 执行中 / 执行失败待重投"，两者混进一个枚举会让
"没算完"被读成"通过"。本文件先固定两件事的边界，再验证状态机不允许多走一步。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.quality.enums import (
    QUALITY_RUN_TRANSITIONS,
    TERMINAL_LIFECYCLES,
    QualityRunLifecycle,
    QualityRunStatus,
)
from app.domain.quality.errors import (
    DomainValidationError,
    InvalidQualityRunTransitionError,
)
from app.domain.quality.models import QualityRun

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 22, 10, 5, tzinfo=UTC)
PROJECT = UUID("00000000-0000-7000-8000-000000000501")
ACTOR = UUID("00000000-0000-7000-8000-000000000502")
SUBJECT = UUID("00000000-0000-7000-8000-000000000503")
RULE_SET = "sha256:" + "a" * 64
#: 六类聚合是既有不变量：产结论时 summary 必须覆盖全部类别（含派生的 OVERALL）。
SUMMARY = {
    "INTEGRITY": {"status": "PASS"},
    "TEMPORAL": {"status": "PASS"},
    "VISUAL": {"status": "PASS"},
    "METADATA": {"status": "PASS"},
    "ANNOTATION": {"status": "PASS"},
    "OVERALL": {"status": "PASS"},
}


def _queued(**overrides: object) -> QualityRun:
    payload: dict[str, object] = {
        "run_id": UUID("00000000-0000-7000-8000-000000000504"),
        "project_id": PROJECT,
        "subject_type": "EPISODE",
        "subject_id": SUBJECT,
        "rule_set_version": RULE_SET,
        "max_attempts": 3,
        "created_at": NOW,
        "created_by": ACTOR,
    }
    payload.update(overrides)
    return QualityRun.queued(**payload)  # type: ignore[arg-type]


def test_lifecycle_enum_and_transition_table_are_closed() -> None:
    """枚举与迁移表必须一一对应：新增状态而不声明边，等于默认允许乱跳。"""
    assert set(QUALITY_RUN_TRANSITIONS) == set(QualityRunLifecycle)
    assert TERMINAL_LIFECYCLES == {
        QualityRunLifecycle.SUCCEEDED,
        QualityRunLifecycle.CANCELLED,
    }
    for lifecycle, targets in QUALITY_RUN_TRANSITIONS.items():
        if lifecycle in TERMINAL_LIFECYCLES:
            assert targets == frozenset(), f"{lifecycle.value} 是终态，不得有出边"
        else:
            assert targets, f"{lifecycle.value} 没有任何合法去向"


def test_queued_run_is_a_request_not_a_conclusion() -> None:
    run = _queued()

    assert run.lifecycle is QualityRunLifecycle.QUEUED
    assert run.status is None
    assert run.score is None
    assert (run.finding_count, run.error_count, run.warn_count) == (0, 0, 0)
    assert run.category_summary == {}
    assert run.started_at is None
    assert run.finished_at is None
    assert run.attempt == 1
    assert run.max_attempts == 3


def test_happy_path_reaches_conclusion_exactly_once() -> None:
    running = _queued().mark_running(now=NOW)
    assert running.lifecycle is QualityRunLifecycle.RUNNING
    assert running.started_at == NOW

    succeeded = running.mark_succeeded(
        status=QualityRunStatus.PASSED,
        score=1.0,
        finding_count=2,
        error_count=0,
        warn_count=1,
        category_summary=SUMMARY,
        finished_at=LATER,
    )

    assert succeeded.lifecycle is QualityRunLifecycle.SUCCEEDED
    assert succeeded.status is QualityRunStatus.PASSED
    assert succeeded.finished_at == LATER
    assert succeeded.finding_count == 2


def test_queued_cannot_jump_straight_to_conclusion() -> None:
    with pytest.raises(InvalidQualityRunTransitionError):
        _queued().mark_succeeded(
            status=QualityRunStatus.PASSED,
            score=1.0,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary={},
            finished_at=LATER,
        )


def test_succeeded_is_terminal() -> None:
    succeeded = (
        _queued()
        .mark_running(now=NOW)
        .mark_succeeded(
            status=QualityRunStatus.PASSED,
            score=1.0,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary=SUMMARY,
            finished_at=LATER,
        )
    )

    for transition in (
        lambda: succeeded.mark_running(now=LATER),
        lambda: succeeded.mark_failed(error_code="x", error_summary="y", now=LATER),
        lambda: succeeded.mark_cancelled(now=LATER),
        lambda: succeeded.schedule_retry(now=LATER),
    ):
        with pytest.raises(InvalidQualityRunTransitionError):
            transition()


def test_failed_run_can_be_retried_with_bumped_attempt() -> None:
    failed = (
        _queued()
        .mark_running(now=NOW)
        .mark_failed(
            error_code="quality_rule_crashed",
            error_summary="规则实现异常",
            now=LATER,
        )
    )
    assert failed.lifecycle is QualityRunLifecycle.FAILED
    assert failed.error_code == "quality_rule_crashed"
    assert failed.finished_at == LATER

    retrying = failed.schedule_retry(now=LATER)
    assert retrying.lifecycle is QualityRunLifecycle.RETRYING
    assert retrying.attempt == 2
    assert retrying.finished_at is None
    assert retrying.error_code == "quality_rule_crashed"

    # 重试后重新执行，仍可走向结论。
    assert retrying.mark_running(now=LATER).lifecycle is QualityRunLifecycle.RUNNING


def test_retried_run_success_clears_previous_failure() -> None:
    """重投后成功：结论不能挂着上一次执行的错误信息。"""
    retrying = (
        _queued()
        .mark_running(now=NOW)
        .mark_failed(error_code="boom", error_summary="规则实现异常", now=NOW)
        .schedule_retry(now=NOW)
    )

    succeeded = retrying.mark_running(now=LATER).mark_succeeded(
        status=QualityRunStatus.PASSED,
        score=1.0,
        finding_count=0,
        error_count=0,
        warn_count=0,
        category_summary=SUMMARY,
        finished_at=LATER,
    )

    assert succeeded.lifecycle is QualityRunLifecycle.SUCCEEDED
    assert succeeded.error_code is None
    assert succeeded.error_summary is None
    assert succeeded.attempt == 2


def test_cancelled_retrying_run_keeps_last_error() -> None:
    retrying = (
        _queued()
        .mark_running(now=NOW)
        .mark_failed(error_code="boom", error_summary="规则实现异常", now=NOW)
        .schedule_retry(now=NOW)
    )

    cancelled = retrying.mark_cancelled(now=LATER)

    assert cancelled.lifecycle is QualityRunLifecycle.CANCELLED
    assert cancelled.error_code == "boom"
    assert cancelled.finished_at == LATER


def test_retry_is_bounded_by_max_attempts() -> None:
    failed = (
        _queued(max_attempts=1)
        .mark_running(now=NOW)
        .mark_failed(error_code="boom", error_summary="失败", now=LATER)
    )

    with pytest.raises(InvalidQualityRunTransitionError):
        failed.schedule_retry(now=LATER)


def test_cancelled_is_terminal_for_running_and_queued() -> None:
    queued_cancelled = _queued().mark_cancelled(now=NOW)
    assert queued_cancelled.lifecycle is QualityRunLifecycle.CANCELLED
    assert queued_cancelled.finished_at == NOW

    running_cancelled = _queued().mark_running(now=NOW).mark_cancelled(now=LATER)
    assert running_cancelled.lifecycle is QualityRunLifecycle.CANCELLED

    with pytest.raises(InvalidQualityRunTransitionError):
        queued_cancelled.mark_running(now=LATER)


def test_succeeded_requires_conclusion_fields() -> None:
    with pytest.raises(DomainValidationError):
        QualityRun(
            run_id=UUID("00000000-0000-7000-8000-000000000505"),
            project_id=PROJECT,
            subject_type="EPISODE",
            subject_id=SUBJECT,
            rule_set_version=RULE_SET,
            status=None,
            score=None,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary={},
            started_at=NOW,
            finished_at=LATER,
            created_at=NOW,
            created_by=ACTOR,
        )


def test_unfinished_run_must_not_carry_conclusion_fields() -> None:
    with pytest.raises(DomainValidationError):
        QualityRun(
            run_id=UUID("00000000-0000-7000-8000-000000000506"),
            project_id=PROJECT,
            subject_type="EPISODE",
            subject_id=SUBJECT,
            rule_set_version=RULE_SET,
            status=QualityRunStatus.PASSED,
            score=1.0,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary={},
            started_at=None,
            finished_at=None,
            created_at=NOW,
            created_by=ACTOR,
            lifecycle=QualityRunLifecycle.QUEUED,
        )


def test_failed_requires_error_code() -> None:
    with pytest.raises(DomainValidationError):
        QualityRun(
            run_id=UUID("00000000-0000-7000-8000-000000000507"),
            project_id=PROJECT,
            subject_type="EPISODE",
            subject_id=SUBJECT,
            rule_set_version=RULE_SET,
            status=None,
            score=None,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary={},
            started_at=NOW,
            finished_at=LATER,
            created_at=NOW,
            created_by=ACTOR,
            lifecycle=QualityRunLifecycle.FAILED,
        )


def test_attempt_bounds_are_enforced() -> None:
    with pytest.raises(DomainValidationError):
        _queued(max_attempts=0)
    with pytest.raises(DomainValidationError):
        _queued(attempt=4, max_attempts=3)
