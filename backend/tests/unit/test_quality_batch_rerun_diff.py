"""质检批量重跑与差异报告（V31-033）：应用层编排。

与 ``test_quality_run_diff.py`` 的分工：那个文件只验证"两次 findings 怎么比"；
本文件验证**编排**——批量入队、取两次运行、产出差异报告、项目隔离与
"未完成运行不可比"。

断言的是数据库事实与返回结构，不 mock 仓储：批量重跑的价值就在于它真的
写了多条 ``QUEUED`` 运行，替身会让"只入队了一条"的缺陷漏过去。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.quality.commands import NewQualityRun
from app.application.quality.diff import QualityRunDiffUseCases
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.domain.quality.diff import DiffKind
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySubjectType,
)
from app.domain.quality.errors import (
    DomainValidationError,
    ResourceNotFoundError,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.quality import QualityFindingModel, QualityRunModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationRecordRepository,
)
from app.infrastructure.repositories.annotation_schemas import (
    SqlAnnotationSchemaRepository,
)
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_policy_repositories import (
    SqlDatasetReportRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.api.dataset_support import seed_dataset_episodes

NOW = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
RULE_SET_VERSION = "sha256:" + "a" * 64


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _use_cases(session: Session) -> QualityRunUseCases:
    return QualityRunUseCases(
        runs=SqlQualityRunRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        annotation_records=SqlAnnotationRecordRepository(session),
        schemas=SqlAnnotationSchemaRepository(session),
        tasks=SqlAnnotationTaskRepository(session),
        registry=default_registry(SqlDatasetReportRepository(session)),
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        uow=session,
    )


def _diff_use_cases(session: Session) -> QualityRunDiffUseCases:
    return QualityRunDiffUseCases(runs=SqlQualityRunRepository(session))


def _episodes(factory: sessionmaker[Session], count: int = 2) -> tuple[ProjectScope, list[UUID]]:
    ids = seed_identity(factory)
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=count,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )
    return ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a), episodes


def _concluded_run(
    factory: sessionmaker[Session],
    *,
    scope: ProjectScope,
    subject_id: UUID,
    status: QualityRunStatus,
    findings: list[tuple[str, str, QualityFindingStatus]],
) -> UUID:
    """造一条 ``SUCCEEDED`` 运行并写入指定 findings，返回 run_id。

    ``findings`` 每项是 ``(rule_id, subject_ref, status)``。
    """
    with factory() as session:
        use_cases = _use_cases(session)
        run = use_cases.enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=subject_id),
        )
        session.commit()
        use_cases.start(scope, run.run_id, now=NOW)
        session.commit()
        session.execute(
            update(QualityRunModel)
            .where(QualityRunModel.id == run.run_id)
            .values(
                lifecycle=QualityRunLifecycle.SUCCEEDED.value,
                status=status.value,
                score=0.0,
                finding_count=len(findings),
                error_count=sum(1 for item in findings if item[2] is QualityFindingStatus.FAIL),
                warn_count=0,
                category_summary={
                    item.value: {
                        "status": "NOT_APPLICABLE",
                        "finding_count": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                        "not_applicable_count": 0,
                        "error_count": 0,
                        "score": None,
                    }
                    for item in QualityCategory
                },
                finished_at=NOW,
            )
        )
        for rule_id, subject_ref, finding_status in findings:
            session.add(
                QualityFindingModel(
                    id=uuid4(),
                    project_id=scope.project_id,
                    run_id=run.run_id,
                    category=QualityCategory.ANNOTATION.value,
                    rule_id=rule_id,
                    rule_version=RULE_SET_VERSION,
                    severity="ERROR",
                    status=finding_status.value,
                    subject_ref=subject_ref,
                    message="规则结论",
                    evidence=None,
                    score=(
                        1.0
                        if finding_status is QualityFindingStatus.PASS
                        else (0.0 if finding_status is QualityFindingStatus.FAIL else None)
                    ),
                    created_at=NOW,
                )
            )
        session.commit()
        return run.run_id


# --------------------------------------------------------------------------- #
# 批量重跑
# --------------------------------------------------------------------------- #


def test_batch_rerun_enqueues_one_run_per_subject(factory: sessionmaker[Session]) -> None:
    scope, episodes = _episodes(factory, count=3)

    with factory() as session:
        runs = _use_cases(session).rerun_batch(scope, episodes)
        session.commit()

    assert len(runs) == 3
    assert {run.subject_id for run in runs} == set(episodes)
    assert all(run.lifecycle is QualityRunLifecycle.QUEUED for run in runs)

    with factory() as session:
        rows = session.scalars(select(QualityRunModel)).all()
        assert len(rows) == 3
        assert {row.lifecycle for row in rows} == {QualityRunLifecycle.QUEUED.value}


def test_batch_rerun_rejects_empty_subject_list(factory: sessionmaker[Session]) -> None:
    """空列表是调用方错误：静默成功会让"重跑 0 条"看起来像已完成。"""
    scope, _ = _episodes(factory)

    with factory() as session:
        with pytest.raises(DomainValidationError, match="至少需要一个对象"):
            _use_cases(session).rerun_batch(scope, [])


def test_batch_rerun_rejects_duplicate_subjects(factory: sessionmaker[Session]) -> None:
    """重复对象会产生两条运行，同一对象被检查两次——拒绝而不是去重。"""
    scope, episodes = _episodes(factory, count=1)
    duplicated = [episodes[0], episodes[0]]

    with factory() as session:
        with pytest.raises(DomainValidationError, match="重复"):
            _use_cases(session).rerun_batch(scope, duplicated)


def test_batch_rerun_enforces_subject_limit(factory: sessionmaker[Session]) -> None:
    scope, episodes = _episodes(factory, count=1)

    with factory() as session:
        with pytest.raises(DomainValidationError, match="超过上限"):
            _use_cases(session).rerun_batch(scope, [uuid4() for _ in range(1001)])
    assert episodes  # 仅为清晰：上限校验先于任何写入


# --------------------------------------------------------------------------- #
# 差异报告
# --------------------------------------------------------------------------- #


def test_diff_reports_new_resolved_unchanged_changed(
    factory: sessionmaker[Session],
) -> None:
    scope, episodes = _episodes(factory, count=1)
    episode_id = episodes[0]
    baseline = _concluded_run(
        factory,
        scope=scope,
        subject_id=episode_id,
        status=QualityRunStatus.FAILED,
        findings=[
            ("rule.a", "", QualityFindingStatus.FAIL),
            ("rule.b", "", QualityFindingStatus.PASS),
            ("rule.c", "", QualityFindingStatus.FAIL),
        ],
    )
    candidate = _concluded_run(
        factory,
        scope=scope,
        subject_id=episode_id,
        status=QualityRunStatus.FAILED,
        findings=[
            ("rule.b", "", QualityFindingStatus.PASS),
            ("rule.c", "", QualityFindingStatus.PASS),
            ("rule.d", "", QualityFindingStatus.FAIL),
        ],
    )

    with factory() as session:
        result = _diff_use_cases(session).compare(scope, baseline, candidate)

    kinds = {item.rule_id: item.kind for item in result.findings}
    assert kinds == {
        "rule.a": DiffKind.RESOLVED,
        "rule.b": DiffKind.UNCHANGED,
        "rule.c": DiffKind.CHANGED,
        "rule.d": DiffKind.NEW,
    }
    assert result.has_regression is True
    assert result.baseline_run_id == baseline
    assert result.candidate_run_id == candidate


def test_diff_rejects_unfinished_baseline(factory: sessionmaker[Session]) -> None:
    """未产出结论的运行不可比：没有可信结论。"""
    scope, episodes = _episodes(factory, count=1)
    episode_id = episodes[0]
    with factory() as session:
        run = _use_cases(session).enqueue(
            scope,
            NewQualityRun(subject_type=QualitySubjectType.EPISODE, subject_id=episode_id),
        )
        session.commit()
        queued_run_id = run.run_id

    candidate = _concluded_run(
        factory, scope=scope, subject_id=episode_id, status=QualityRunStatus.PASSED, findings=[]
    )

    with factory() as session:
        with pytest.raises(DomainValidationError, match="尚未产出结论"):
            _diff_use_cases(session).compare(scope, queued_run_id, candidate)


def test_diff_rejects_cross_subject_runs(factory: sessionmaker[Session]) -> None:
    """两次运行必须检查同一对象；否则差异没有意义。"""
    scope, episodes = _episodes(factory, count=2)
    baseline = _concluded_run(
        factory,
        scope=scope,
        subject_id=episodes[0],
        status=QualityRunStatus.PASSED,
        findings=[],
    )
    candidate = _concluded_run(
        factory,
        scope=scope,
        subject_id=episodes[1],
        status=QualityRunStatus.PASSED,
        findings=[],
    )

    with factory() as session:
        with pytest.raises(DomainValidationError, match="同一对象"):
            _diff_use_cases(session).compare(scope, baseline, candidate)


def test_diff_rejects_unknown_run(factory: sessionmaker[Session]) -> None:
    scope, _ = _episodes(factory, count=1)

    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            _diff_use_cases(session).compare(scope, uuid4(), uuid4())


def test_diff_cross_project_run_is_not_visible(factory: sessionmaker[Session]) -> None:
    scope, episodes = _episodes(factory, count=1)
    run_id = _concluded_run(
        factory,
        scope=scope,
        subject_id=episodes[0],
        status=QualityRunStatus.PASSED,
        findings=[],
    )
    foreign = ProjectScope(project_id=uuid4(), actor_id=scope.actor_id)

    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            _diff_use_cases(session).compare(foreign, run_id, run_id)


def test_diff_same_run_yields_all_unchanged(factory: sessionmaker[Session]) -> None:
    scope, episodes = _episodes(factory, count=1)
    run_id = _concluded_run(
        factory,
        scope=scope,
        subject_id=episodes[0],
        status=QualityRunStatus.PASSED,
        findings=[("rule.a", "", QualityFindingStatus.PASS)],
    )

    with factory() as session:
        result = _diff_use_cases(session).compare(scope, run_id, run_id)

    assert [item.kind for item in result.findings] == [DiffKind.UNCHANGED]
    assert result.has_regression is False
