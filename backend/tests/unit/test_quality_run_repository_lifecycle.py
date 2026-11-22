"""质量仓储的结论视图（V31-030）。

``quality_runs`` 现在同时承载"排队/执行/失败/结论"四类事实，但 HTTP 契约
只暴露结论。本文件固定仓储边界：未完成的运行不得进入结论视图，也不得经
``add`` 混入。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import QualityRunLifecycle, QualityRunStatus, QualitySubjectType
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.models import QualityRun
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.quality import QualityRunModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64


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
            Project(id=project_id, code="quality-lifecycle", name="生命周期项目"),
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


def _conclusion(project_id: UUID, actor: UUID, subject_id: UUID, *, created_at: datetime):
    return QualityRun(
        run_id=uuid4(),
        project_id=project_id,
        subject_type="EPISODE",
        subject_id=subject_id,
        rule_set_version=_SHA,
        status=QualityRunStatus.PASSED,
        score=1.0,
        finding_count=0,
        error_count=0,
        warn_count=0,
        category_summary=summarize_findings(()).category_payload(),
        started_at=created_at,
        finished_at=created_at,
        created_at=created_at,
        created_by=actor,
    )


def _queued_row(project_id: UUID, actor: UUID, subject_id: UUID, *, created_at: datetime):
    """直接落一条排队中的行：语义上不经 ``add``（结论视图的写入入口）。"""
    return QualityRunModel(
        id=uuid4(),
        project_id=project_id,
        subject_type="EPISODE",
        subject_id=subject_id,
        rule_set_version=_SHA,
        status=None,
        score=None,
        finding_count=0,
        error_count=0,
        warn_count=0,
        category_summary={},
        started_at=None,
        finished_at=None,
        created_at=created_at,
        created_by=actor,
        lifecycle=QualityRunLifecycle.QUEUED.value,
        attempt=1,
        max_attempts=3,
    )


def test_add_rejects_unfinished_run(session: Session) -> None:
    project_id: UUID = session.info["project_id"]
    actor: UUID = session.info["actor"]
    scope = ProjectScope(project_id=project_id, actor_id=actor)
    queued = QualityRun.queued(
        run_id=uuid4(),
        project_id=project_id,
        subject_type="EPISODE",
        subject_id=uuid4(),
        rule_set_version=_SHA,
        created_at=NOW,
        created_by=actor,
        max_attempts=3,
    )

    with pytest.raises(DomainValidationError, match="只能追加已产出结论的运行"):
        SqlQualityRunRepository(session).add(scope, queued)


def test_unfinished_run_is_invisible_in_conclusion_view(session: Session) -> None:
    project_id: UUID = session.info["project_id"]
    actor: UUID = session.info["actor"]
    scope = ProjectScope(project_id=project_id, actor_id=actor)
    subject_id = uuid4()
    repo = SqlQualityRunRepository(session)

    conclusion = _conclusion(project_id, actor, subject_id, created_at=NOW - timedelta(hours=1))
    repo.add(scope, conclusion)
    queued = _queued_row(project_id, actor, subject_id, created_at=NOW)
    session.add(queued)
    session.commit()

    assert repo.get(scope, queued.id) is None
    listed = repo.list(scope, subject_type=QualitySubjectType.EPISODE, subject_id=subject_id)
    assert [run.run_id for run in listed] == [conclusion.run_id]

    latest = repo.latest_for_subjects(
        scope, subject_type=QualitySubjectType.EPISODE, subject_ids=[subject_id]
    )
    assert [run.run_id for run in latest] == [conclusion.run_id]


def test_succeeded_run_reads_back_with_lifecycle_metadata(session: Session) -> None:
    project_id: UUID = session.info["project_id"]
    actor: UUID = session.info["actor"]
    scope = ProjectScope(project_id=project_id, actor_id=actor)
    subject_id = uuid4()
    repo = SqlQualityRunRepository(session)
    run = _conclusion(project_id, actor, subject_id, created_at=NOW)
    repo.add(scope, run)
    session.commit()

    loaded = repo.get(scope, run.run_id)

    assert loaded is not None
    assert loaded.lifecycle is QualityRunLifecycle.SUCCEEDED
    assert (loaded.attempt, loaded.max_attempts) == (1, 1)
    assert loaded.error_code is None
