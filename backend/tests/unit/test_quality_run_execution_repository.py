"""质量运行的执行写入（V31-031）。

V31-030 落地了生命周期与结论正交，但 ``add`` 只接受**已产出结论**的运行，
排队/执行/失败/重投没有任何写入入口。本文件固定执行路径的三个边界：

- ``enqueue`` 只写入 ``QUEUED``，不能借此塞进结论；
- ``transition`` 以 ``lifecycle`` 做 CAS：并发写者已经推进过的行不得被覆盖；
- 状态机之外的一条边也不许走（跳步等于伪造执行证据）。

断言的是持久化后的数据库事实，不是内存对象。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySeverity,
)
from app.domain.quality.errors import (
    DomainValidationError,
    InvalidQualityRunTransitionError,
    QualityRunConflictError,
)
from app.domain.quality.models import QualityFinding, QualityFindingDraft, QualityRun
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.quality import QualityFindingModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 22, 13, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=3)
_SHA = "sha256:" + "b" * 64


@pytest.fixture()
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    project_id = uuid4()
    actor = uuid4()
    current = session_factory()
    current.add_all(
        [
            Project(id=project_id, code="quality-exec", name="执行项目"),
            User(id=actor, display_name="质检员"),
        ]
    )
    current.commit()
    current.info["project_id"] = project_id
    current.info["actor"] = actor
    try:
        yield current
    finally:
        current.close()


def _scope(session: Session) -> ProjectScope:
    return ProjectScope(
        project_id=session.info["project_id"],
        actor_id=session.info["actor"],
    )


def _queued(session: Session, *, max_attempts: int = 3) -> QualityRun:
    return QualityRun.queued(
        run_id=uuid4(),
        project_id=session.info["project_id"],
        subject_type="EPISODE",
        subject_id=uuid4(),
        rule_set_version=_SHA,
        created_at=NOW,
        created_by=session.info["actor"],
        max_attempts=max_attempts,
    )


def _finding(run: QualityRun, *, status: QualityFindingStatus) -> QualityFinding:
    return QualityFinding.from_draft(
        finding_id=uuid4(),
        project_id=run.project_id,
        run_id=run.run_id,
        draft=QualityFindingDraft(
            rule_id="episode.video.duration",
            rule_version="1",
            category=QualityCategory.VISUAL,
            severity=QualitySeverity.WARN,
            status=status,
            message="时长低于建议阈值",
            score=0.5,
        ),
        created_at=NOW,
    )


def test_enqueue_writes_queued_row(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)

    repo.enqueue(scope, queued)
    session.commit()

    loaded = repo.find_any(scope, queued.run_id)
    assert loaded is not None
    assert loaded.lifecycle is QualityRunLifecycle.QUEUED
    assert loaded.status is None
    assert loaded.started_at is None
    assert (loaded.attempt, loaded.max_attempts) == (1, 3)
    # 结论视图看不到排队的运行。
    assert repo.get(scope, queued.run_id) is None


def test_enqueue_rejects_concluded_run(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    concluded = QualityRun(
        run_id=uuid4(),
        project_id=session.info["project_id"],
        subject_type="EPISODE",
        subject_id=uuid4(),
        rule_set_version=_SHA,
        status=QualityRunStatus.PASSED,
        score=1.0,
        finding_count=0,
        error_count=0,
        warn_count=0,
        category_summary=summarize_findings(()).category_payload(),
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
        created_by=session.info["actor"],
    )

    with pytest.raises(DomainValidationError, match="只能入队"):
        repo.enqueue(scope, concluded)


def test_transition_persists_running_and_keeps_conclusion_view_empty(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)

    running = repo.transition(
        scope, queued.mark_running(now=LATER), expected=QualityRunLifecycle.QUEUED
    )
    session.commit()

    loaded = repo.find_any(scope, queued.run_id)
    assert loaded is not None
    assert loaded.lifecycle is QualityRunLifecycle.RUNNING
    assert loaded.started_at == LATER
    assert running.lifecycle is QualityRunLifecycle.RUNNING
    assert repo.get(scope, queued.run_id) is None


def test_transition_rejects_stale_expected_lifecycle(session: Session) -> None:
    """两个 Worker 抢同一行：后到的写者必须失败，不能覆盖已推进的状态。"""
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)
    repo.transition(scope, queued.mark_running(now=LATER), expected=QualityRunLifecycle.QUEUED)

    with pytest.raises(QualityRunConflictError):
        repo.transition(
            scope,
            queued.mark_running(now=LATER),
            expected=QualityRunLifecycle.QUEUED,
        )


def test_transition_rejects_edge_outside_state_machine(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)

    with pytest.raises(InvalidQualityRunTransitionError):
        repo.transition(scope, queued, expected=QualityRunLifecycle.QUEUED)


def test_transition_to_conclusion_writes_findings_and_is_visible(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)
    running = repo.transition(
        scope,
        queued.mark_running(now=NOW),
        expected=QualityRunLifecycle.QUEUED,
    )

    warn = _finding(running, status=QualityFindingStatus.FAIL)
    summary = summarize_findings((warn,))
    succeeded = running.mark_succeeded(
        status=summary.status,
        score=summary.score,
        finding_count=summary.finding_count,
        error_count=summary.error_count,
        warn_count=summary.warn_count,
        category_summary=summary.category_payload(),
        finished_at=LATER,
        findings=(warn,),
    )
    repo.transition(scope, succeeded, expected=QualityRunLifecycle.RUNNING)
    session.commit()

    loaded = repo.get(scope, queued.run_id)
    assert loaded is not None
    assert loaded.lifecycle is QualityRunLifecycle.SUCCEEDED
    assert loaded.status is summary.status
    stored = session.scalar(
        select(func.count())
        .select_from(QualityFindingModel)
        .where(QualityFindingModel.run_id == queued.run_id)
    )
    assert stored == 1


def test_transition_failure_and_retry_keep_attempt_history(session: Session) -> None:
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)
    running = repo.transition(
        scope,
        queued.mark_running(now=NOW),
        expected=QualityRunLifecycle.QUEUED,
    )

    failed = repo.transition(
        scope,
        running.mark_failed(error_code="rule_crashed", error_summary="规则实现异常", now=LATER),
        expected=QualityRunLifecycle.RUNNING,
    )
    retrying = repo.transition(
        scope,
        failed.schedule_retry(now=LATER),
        expected=QualityRunLifecycle.FAILED,
    )
    session.commit()

    loaded = repo.find_any(scope, queued.run_id)
    assert loaded is not None
    assert loaded.lifecycle is QualityRunLifecycle.RETRYING
    assert loaded.attempt == 2
    assert loaded.finished_at is None
    # 重投保留上一次失败原因，作为"为什么又跑一遍"的依据。
    assert loaded.error_code == "rule_crashed"
    assert retrying.attempt == 2
    assert loaded.run_id == queued.run_id


def test_find_any_is_project_scoped(session: Session) -> None:
    """跨项目 ID 查不到：读取按项目作用域过滤，不靠调用方自觉。"""
    repo = SqlQualityRunRepository(session)
    scope = _scope(session)
    queued = _queued(session)
    repo.enqueue(scope, queued)

    other = ProjectScope(project_id=UUID(int=1), actor_id=session.info["actor"])

    assert repo.find_any(other, queued.run_id) is None
