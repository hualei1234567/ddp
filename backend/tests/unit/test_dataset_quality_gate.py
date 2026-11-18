"""数据集质量门禁领域/适配单测。

覆盖：最新运行口径、无运行 / INCOMPLETE / 未通过不得当通过、阈值边界、
选择过滤与排除码、批量查询（一次 SQL、无 N+1）、默认不启用行为不变。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.datasets.quality_gate import EpisodeQualityGate
from app.domain.datasets.enums import ExclusionCode
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.quality_gate import (
    EpisodeQualityVerdict,
    evaluate_quality_gate,
)
from app.domain.datasets.selection import SelectionFilters, evaluate_selection
from app.domain.identity.models import ProjectScope
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import QualityRunStatus, QualitySubjectType
from app.domain.quality.models import QualityRun
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import identity as _identity  # noqa: F401
from app.infrastructure.db.models import quality as _quality  # noqa: F401
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from tests.unit.test_dataset_domain import candidate

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64


def _verdict(
    episode_id: UUID,
    status: QualityRunStatus | str,
    score: float | None,
    *,
    run_id: UUID | None = None,
) -> EpisodeQualityVerdict:
    return EpisodeQualityVerdict(
        episode_id=episode_id,
        run_id=run_id or uuid4(),
        status=status,
        score=score,
        created_at=NOW,
    )


def test_gate_passes_only_latest_passed_run_at_or_above_threshold() -> None:
    ok = uuid4()
    decisions = evaluate_quality_gate(
        (ok,),
        {ok: _verdict(ok, QualityRunStatus.PASSED, 0.8)},
        min_score=0.8,
    )
    assert decisions[0].passed
    assert decisions[0].code is None


@pytest.mark.parametrize(
    ("status", "score", "expected"),
    [
        (QualityRunStatus.INCOMPLETE, None, ExclusionCode.QUALITY_RUN_NOT_PASSED),
        (QualityRunStatus.FAILED, 0.9, ExclusionCode.QUALITY_RUN_NOT_PASSED),
        (QualityRunStatus.PASSED, None, ExclusionCode.QUALITY_SCORE_MISSING),
        (QualityRunStatus.PASSED, 0.7999, ExclusionCode.QUALITY_SCORE_BELOW_THRESHOLD),
    ],
)
def test_gate_never_treats_incomplete_failed_or_missing_score_as_pass(
    status: QualityRunStatus,
    score: float | None,
    expected: ExclusionCode,
) -> None:
    episode_id = uuid4()
    decisions = evaluate_quality_gate(
        (episode_id,),
        {episode_id: _verdict(episode_id, status, score)},
        min_score=0.8,
    )
    assert not decisions[0].passed
    assert decisions[0].code is expected


def test_gate_blocks_episode_without_any_run() -> None:
    episode_id = uuid4()
    decisions = evaluate_quality_gate((episode_id,), {}, min_score=0.8)
    assert decisions[0].code is ExclusionCode.QUALITY_RUN_MISSING
    assert decisions[0].detail == "缺少统一质量运行结论"


def test_gate_deduplicates_episode_ids_and_keeps_input_order() -> None:
    first, second = uuid4(), uuid4()
    decisions = evaluate_quality_gate(
        (first, second, first),
        {
            first: _verdict(first, QualityRunStatus.PASSED, 1.0),
            second: _verdict(second, QualityRunStatus.PASSED, 0.5),
        },
        min_score=0.8,
    )
    assert [item.episode_id for item in decisions] == [first, second]


def test_selection_gate_off_by_default_keeps_existing_behavior() -> None:
    outcome = evaluate_selection(
        (candidate(0), candidate(1)),
        filters=SelectionFilters(),
        usage_policy="TRAINING_ALLOWED",
    )
    assert outcome.selected_count == 2
    assert outcome.excluded == ()


def test_selection_gate_filters_and_reports_reason_codes() -> None:
    below = candidate(0)
    incomplete = candidate(1)
    missing = candidate(2)
    passed = candidate(3)
    verdicts = {
        below.episode_id: _verdict(below.episode_id, QualityRunStatus.PASSED, 0.79),
        incomplete.episode_id: _verdict(incomplete.episode_id, QualityRunStatus.INCOMPLETE, None),
        passed.episode_id: _verdict(passed.episode_id, QualityRunStatus.PASSED, 0.8),
    }
    outcome = evaluate_selection(
        (below, incomplete, missing, passed),
        filters=SelectionFilters(min_quality_score=0.8),
        usage_policy="TRAINING_ALLOWED",
        quality_verdicts=verdicts,
    )
    assert [item.episode_id for item in outcome.selected] == [passed.episode_id]
    codes = {item.episode_id: item.code for item in outcome.excluded}
    assert codes[below.episode_id] is ExclusionCode.QUALITY_SCORE_BELOW_THRESHOLD
    assert codes[incomplete.episode_id] is ExclusionCode.QUALITY_RUN_NOT_PASSED
    assert codes[missing.episode_id] is ExclusionCode.QUALITY_RUN_MISSING


def test_selection_gate_fails_closed_when_verdicts_not_supplied() -> None:
    with pytest.raises(DomainValidationError) as excinfo:
        evaluate_selection(
            (candidate(0),),
            filters=SelectionFilters(min_quality_score=0.8),
            usage_policy="TRAINING_ALLOWED",
        )
    assert "min_quality_score" in str(excinfo.value)


def test_selection_filter_rejects_out_of_range_threshold() -> None:
    with pytest.raises(DomainValidationError):
        SelectionFilters.from_mapping({"min_quality_score": 1.5})
    with pytest.raises(DomainValidationError):
        SelectionFilters.from_mapping({"min_quality_score": True})


class _CountingRuns:
    """显式替身：验证门禁只做一次批量读取，不按 Episode 逐条查询。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, ...]] = []
        self._rows: dict[UUID, QualityRun] = {}

    def add(self, run: QualityRun) -> None:
        self._rows[run.subject_id] = run

    def latest_for_subjects(self, scope, *, subject_type, subject_ids):
        self.calls.append(tuple(subject_ids))
        return tuple(self._rows[item] for item in subject_ids if item in self._rows)


def _run(
    project_id: UUID,
    episode_id: UUID,
    *,
    status: QualityRunStatus,
    score: float | None,
    created_at: datetime,
    created_by: UUID,
    run_id: UUID | None = None,
) -> QualityRun:
    return QualityRun(
        run_id=run_id or uuid4(),
        project_id=project_id,
        subject_type="EPISODE",
        subject_id=episode_id,
        rule_set_version=_SHA,
        status=status,
        score=score,
        finding_count=0,
        error_count=0,
        warn_count=0,
        category_summary=summarize_findings(()).category_payload(),
        started_at=created_at,
        finished_at=created_at,
        created_at=created_at,
        created_by=created_by,
    )


def test_episode_quality_gate_reads_all_episodes_in_one_repository_call() -> None:
    scope = ProjectScope(project_id=uuid4(), actor_id=uuid4())
    actor = uuid4()
    runs = _CountingRuns()
    episode_ids = [uuid4() for _ in range(5)]
    for index, episode_id in enumerate(episode_ids):
        runs.add(
            _run(
                scope.project_id,
                episode_id,
                status=QualityRunStatus.PASSED,
                score=0.5 + index / 10,
                created_at=NOW,
                created_by=actor,
            )
        )
    gate = EpisodeQualityGate(runs)
    verdicts = gate.verdicts(scope, episode_ids)
    assert len(runs.calls) == 1
    assert set(runs.calls[0]) == set(episode_ids)
    assert {item.episode_id for item in verdicts.values()} == set(episode_ids)


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


def test_repository_latest_for_subjects_uses_created_at_then_id_and_one_query(
    session_factory: sessionmaker[Session],
) -> None:
    project_id = uuid4()
    other_project = uuid4()
    first, second = uuid4(), uuid4()
    older = NOW - timedelta(hours=1)
    tie = NOW
    actor = uuid4()
    other_actor = uuid4()
    scope = ProjectScope(project_id=project_id, actor_id=actor)
    cross_scope = ProjectScope(project_id=other_project, actor_id=other_actor)
    session = session_factory()
    session.add_all(
        [
            Project(id=project_id, code="quality-gate-a", name="门禁项目 A"),
            Project(id=other_project, code="quality-gate-b", name="门禁项目 B"),
            User(id=actor, display_name="门禁演员 A"),
            User(id=other_actor, display_name="门禁演员 B"),
        ]
    )
    session.commit()
    repo = SqlQualityRunRepository(session)
    # 同一对象：旧运行 PASS，新运行 INCOMPLETE → 应取 INCOMPLETE。
    repo.add(
        scope,
        _run(
            project_id,
            first,
            status=QualityRunStatus.PASSED,
            score=0.9,
            created_at=older,
            created_by=actor,
        ),
    )
    repo.add(
        scope,
        _run(
            project_id,
            first,
            status=QualityRunStatus.INCOMPLETE,
            score=None,
            created_at=NOW,
            created_by=actor,
        ),
    )
    # 同一 created_at：按 id 倒序取大的。
    low_id, high_id = UUID(int=1), UUID(int=2)
    repo.add(
        scope,
        _run(
            project_id,
            second,
            status=QualityRunStatus.PASSED,
            score=0.1,
            created_at=tie,
            created_by=actor,
            run_id=low_id,
        ),
    )
    repo.add(
        scope,
        _run(
            project_id,
            second,
            status=QualityRunStatus.PASSED,
            score=0.2,
            created_at=tie,
            created_by=actor,
            run_id=high_id,
        ),
    )
    # 跨项目同 subject_id 不得被读到。
    repo.add(
        cross_scope,
        _run(
            other_project,
            first,
            status=QualityRunStatus.PASSED,
            score=1.0,
            created_at=NOW + timedelta(hours=1),
            created_by=other_actor,
        ),
    )
    session.commit()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", _record)
    try:
        latest = repo.latest_for_subjects(
            scope,
            subject_type=QualitySubjectType.EPISODE,
            subject_ids=(first, second),
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _record)

    by_subject = {item.subject_id: item for item in latest}
    assert len(latest) == 2
    assert by_subject[first].status is QualityRunStatus.INCOMPLETE
    assert by_subject[first].score is None
    assert by_subject[second].run_id == high_id
    assert sum("quality_runs" in item for item in statements) == 1
    session.close()
