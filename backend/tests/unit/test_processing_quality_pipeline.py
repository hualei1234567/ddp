"""QUALITY 阶段与真实质量引擎的流水线集成（N4-005，真实 SQLite）。

覆盖：一次运行对多个 Episode 逐个质检并固化规则集版本；落库失败后重试不会
产生重复/矛盾的 quality_run；规则执行异常是永久失败；跨项目 Episode 引用
不会被读取。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.pipeline import (
    PipelineOutcomeStatus,
    ProcessingPipeline,
    SkippedStage,
    default_handlers,
)
from app.application.processing.quality_stage import QualityCheckHandler
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.application.quality.episode_checks import EpisodeQualityChecks
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import (
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.quality.errors import QualityEngineError
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.quality import QualityRunModel
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
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.api.dataset_support import seed_dataset_episodes

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
_RULE_VERSION_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_RECORD_LABELS = [
    {
        "label_key": "action",
        "value": "PICK",
        "source": "HUMAN",
        "label_status": "CONFIRMED",
    }
]


class FakeInspector:
    def __init__(self, sizes: dict[str, int] | None = None) -> None:
        self._sizes = sizes or {}

    def object_size(self, object_key: str) -> int | None:
        return self._sizes.get(object_key)


class FlakyGateway:
    """第一次落库后抛异常，模拟基础设施故障；用于验证回滚与重试幂等。"""

    def __init__(self, inner: EpisodeQualityChecks) -> None:
        self._inner = inner
        self.fail_next_persist = True

    def prepare_episode(self, scope: ProjectScope, episode_id: UUID, *, now: datetime):
        return self._inner.prepare_episode(scope, episode_id, now=now)

    def persist_prepared(self, scope: ProjectScope, runs: tuple) -> None:
        self._inner.persist_prepared(scope, runs)
        if self.fail_next_persist:
            self.fail_next_persist = False
            raise RuntimeError("模拟质量结论落库故障")


class FailingGateway:
    def prepare_episode(self, scope: ProjectScope, episode_id: UUID, *, now: datetime):
        raise QualityEngineError("规则源与声明不一致")

    def persist_prepared(self, scope: ProjectScope, runs: tuple) -> None:  # pragma: no cover
        raise AssertionError("规则执行已失败，不得落库")


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


def _quality_use_cases(session: Session) -> QualityRunUseCases:
    """与 Worker 相同的装配；测试不复制规则，只复用现有仓储。"""
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


def _pipeline(
    session: Session,
    *,
    inspector: FakeInspector,
    gateway: object | None = None,
) -> ProcessingPipeline:
    runs = SqlProcessingRunRepository(session)
    quality_check = QualityCheckHandler(
        episode_assets=SqlEpisodeRawAssetRepository(session),
        quality=gateway or EpisodeQualityChecks(_quality_use_cases(session)),
        uow=session,
    )
    return ProcessingPipeline(
        runs,
        SqlProcessingRunStepRepository(session),
        ProcessingWorkerUseCases(
            runs,
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
        default_handlers(
            inspector,
            metadata_extraction=SkippedStage("质量流水线测试不装配元数据解析"),
            format_parsing=SkippedStage("质量流水线测试不装配格式解析"),
            time_alignment=SkippedStage("质量流水线测试不装配时间对齐"),
            quality_check=quality_check,
        ),
        session,
    )


def _session_services(factory: sessionmaker[Session]) -> tuple[Session, ProcessingRunUseCases]:
    session = factory()
    runs = SqlProcessingRunRepository(session)
    use_cases = ProcessingRunUseCases(
        runs,
        SqlProcessingRunStepRepository(session),
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
    )
    return session, use_cases


def _manifest(project_id: UUID, episode_id: UUID) -> str:
    return f"canonical/{project_id}/{episode_id}/1/manifest.json"


def _quality_rows(factory: sessionmaker[Session], project_id: UUID) -> list[QualityRunModel]:
    with factory() as session:
        return list(
            session.scalars(select(QualityRunModel).where(QualityRunModel.project_id == project_id))
        )


def test_pipeline_checks_every_episode_and_persists_rule_version(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=2,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )
    refs = tuple(_manifest(ids.project_a, episode_id) for episode_id in episodes)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=refs),
        )
        pipeline = _pipeline(
            session,
            inspector=FakeInspector({ref: 10 for ref in refs}),
        )

        outcome = pipeline.execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        steps = use_cases.list_steps(scope, run.run_id)
        quality_step = next(
            step for step in steps if step.stage == ProcessingStage.QUALITY_PRE_CHECK.value
        )
        assert quality_step.status == ProcessingStepStatus.SUCCEEDED.value
        summary = quality_step.log_summary or ""
        assert "检查 2 个 Episode" in summary
        versions = _RULE_VERSION_PATTERN.findall(summary)
        assert len(versions) == 1

        rows = _quality_rows(factory, ids.project_a)
        assert {row.subject_id for row in rows} == set(episodes)
        assert {row.subject_type for row in rows} == {"EPISODE"}
        assert {row.rule_set_version for row in rows} == {versions[0]}
        current = use_cases.get(scope, run.run_id)
        assert set(current.output_refs) == {f"quality-run/{row.id}" for row in rows}
        assert current.status == ProcessingRunStatus.SUCCEEDED.value
    finally:
        session.close()


def test_persist_failure_rolls_back_and_retry_is_idempotent(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    episode_id = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=1,
        record_labels=_RECORD_LABELS,
    )[0]
    ref = _manifest(ids.project_a, episode_id)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=(ref,)),
        )
        gateway = FlakyGateway(EpisodeQualityChecks(_quality_use_cases(session)))
        pipeline = _pipeline(
            session,
            inspector=FakeInspector({ref: 10}),
            gateway=gateway,
        )

        first = pipeline.execute(scope, run.run_id, now=NOW)

        assert first.status == PipelineOutcomeStatus.FAILED.value
        assert first.error_code == "quality_persist_failed"
        assert first.retryable is True
        # 落库失败必须整体回滚：不能留下"部分检查过"的结论。
        assert _quality_rows(factory, ids.project_a) == []

        second = pipeline.execute(scope, run.run_id, now=NOW)

        assert second.status == PipelineOutcomeStatus.SUCCEEDED.value
        rows = _quality_rows(factory, ids.project_a)
        assert len(rows) == 1
        assert rows[0].subject_id == episode_id
        quality_steps = [
            step
            for step in use_cases.list_steps(scope, run.run_id)
            if step.stage == ProcessingStage.QUALITY_PRE_CHECK.value
        ]
        assert [step.attempt for step in quality_steps] == [1, 2]
        assert quality_steps[-1].status == ProcessingStepStatus.SUCCEEDED.value
    finally:
        session.close()


def test_rule_engine_error_is_permanent_failure(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    episode_id = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=1,
        record_labels=_RECORD_LABELS,
    )[0]
    ref = _manifest(ids.project_a, episode_id)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=(ref,)),
        )
        pipeline = _pipeline(
            session,
            inspector=FakeInspector({ref: 10}),
            gateway=FailingGateway(),
        )

        outcome = pipeline.execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.FAILED.value
        assert outcome.error_code == "quality_engine_error"
        assert outcome.retryable is False
        assert outcome.failed_stage == ProcessingStage.QUALITY_PRE_CHECK.value
        assert _quality_rows(factory, ids.project_a) == []
    finally:
        session.close()


def test_cross_project_episode_reference_is_not_read(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    foreign_episode = seed_dataset_episodes(
        factory,
        ids.project_b,
        ids.owner_a,
        count=1,
        record_labels=_RECORD_LABELS,
    )[0]
    # 项目 A 的 Key 前缀 + 项目 B 的 Episode ID：解析阶段看似合法，但质量引擎
    # 按 A 的作用域读取时必然查不到，不能跨项目读取。
    ref = _manifest(ids.project_a, foreign_episode)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=(ref,)),
        )
        pipeline = _pipeline(session, inspector=FakeInspector({ref: 10}))

        outcome = pipeline.execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.FAILED.value
        assert outcome.error_code == "quality_episode_not_found"
        assert outcome.retryable is False
        assert _quality_rows(factory, ids.project_a) == []
        assert _quality_rows(factory, ids.project_b) == []
    finally:
        session.close()
