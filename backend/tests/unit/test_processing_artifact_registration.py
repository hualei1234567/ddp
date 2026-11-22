"""产物目录登记：阶段产出 → ``processing_artifacts``（V31-016）。

阶段处理器是纯函数，只**返回**产物描述（:class:`StageArtifact`）；真正的
落库由编排器在同一事务内完成。本文件验证：

- 阶段返回的产物描述被登记为 ``processing_artifacts`` 行，字段完整；
- ``output_refs`` 仍同步累积（兼容投影，既有调用方不变）；
- 同一 ``(run, stage, attempt)`` 重跑不会重复登记（幂等）；
- 阶段未返回产物时目录为空，不伪造记录。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.pipeline import (
    PipelineOutcomeStatus,
    ProcessingPipeline,
    default_handlers,
)
from app.application.processing.stages import (
    SkippedStage,
    StageArtifact,
    StageContext,
    StageOutcome,
)
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.processing_artifacts import (
    SqlProcessingArtifactRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


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


class _Inspector:
    def __init__(self, sizes: dict[str, int]) -> None:
        self._sizes = sizes

    def object_size(self, object_key: str) -> int | None:
        return self._sizes.get(object_key)


def _pipeline(session: Session, handlers: dict) -> ProcessingPipeline:
    runs = SqlProcessingRunRepository(session)
    return ProcessingPipeline(
        runs,
        SqlProcessingRunStepRepository(session),
        ProcessingWorkerUseCases(
            runs,
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
        handlers,
        session,
        artifacts=SqlProcessingArtifactRepository(session),
    )


def _use_cases(session: Session) -> ProcessingRunUseCases:
    return ProcessingRunUseCases(
        SqlProcessingRunRepository(session),
        SqlProcessingRunStepRepository(session),
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        raw_assets=SqlRawAssetRepository(session),
    )


def _handlers_emitting(artifact: StageArtifact | None, *, asset_key: str) -> dict:
    """只让 ``METADATA_EXTRACTION`` 产出产物，其余阶段显式跳过。"""

    class _Emitting:
        def __call__(self, context: StageContext) -> StageOutcome:
            if artifact is None:
                return StageOutcome(
                    status=ProcessingStepStatus.SUCCEEDED.value,
                    log_summary="无产物",
                )
            return StageOutcome(
                status=ProcessingStepStatus.SUCCEEDED.value,
                log_summary="产出 1 个元数据对象",
                output_refs=(artifact.object_key,),
                artifacts=(artifact,),
            )

    return default_handlers(
        _Inspector({asset_key: 1024}),
        metadata_extraction=_Emitting(),
        format_parsing=SkippedStage("测试不装配格式解析"),
        time_alignment=SkippedStage("测试不装配时间对齐"),
        calibration_validation=SkippedStage("测试不装配标定校验"),
        episode_segmentation=SkippedStage("测试不装配分段"),
        quality_check=SkippedStage("测试不装配质量引擎"),
    )


def test_stage_artifacts_are_registered(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_a}/asset",),
            ),
        )
        artifact = StageArtifact(
            kind=ProcessingArtifactKind.METADATA,
            object_key=f"processing-run/{ids.project_a}/{run.run_id}/metadata/0000-mp4.json",
            schema_version="ddp.processing.metadata/1.0.0",
            content_type="application/json",
            size_bytes=512,
            checksum_sha256="b" * 64,
            source_ref=f"raw/{ids.project_a}/asset",
        )
        pipeline = _pipeline(
            session, _handlers_emitting(artifact, asset_key=f"raw/{ids.project_a}/asset")
        )

        outcome = pipeline.execute(scope, run.run_id)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        stored = SqlProcessingArtifactRepository(session).list(scope, run_id=run.run_id)

    assert len(stored) == 1
    record = stored[0]
    assert record.kind == ProcessingArtifactKind.METADATA.value
    assert record.stage == ProcessingStage.METADATA_EXTRACTION.value
    assert record.attempt == 1
    assert record.object_key == artifact.object_key
    assert record.schema_version == "ddp.processing.metadata/1.0.0"
    assert record.content_type == "application/json"
    assert record.size_bytes == 512
    assert record.checksum_sha256 == "b" * 64
    assert record.source_ref == f"raw/{ids.project_a}/asset"
    assert record.project_id == ids.project_a
    assert record.run_id == run.run_id
    assert record.created_by == ids.editor_a


def test_output_refs_projection_still_populated(factory: sessionmaker[Session]) -> None:
    """``output_refs`` 是兼容投影：产物登记后它仍然累积，既有调用方不变。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_a}/asset",),
            ),
        )
        artifact = StageArtifact(
            kind=ProcessingArtifactKind.METADATA,
            object_key=f"processing-run/{ids.project_a}/{run.run_id}/metadata/0000-mp4.json",
            schema_version="ddp.processing.metadata/1.0.0",
            content_type="application/json",
            size_bytes=512,
        )
        pipeline = _pipeline(
            session, _handlers_emitting(artifact, asset_key=f"raw/{ids.project_a}/asset")
        )

        pipeline.execute(scope, run.run_id)
        final = use_cases.get(scope, run.run_id)

    assert artifact.object_key in final.output_refs


def test_no_artifacts_means_empty_catalog(factory: sessionmaker[Session]) -> None:
    """阶段不返回产物时目录为空，不伪造记录。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_a}/asset",),
            ),
        )
        pipeline = _pipeline(
            session, _handlers_emitting(None, asset_key=f"raw/{ids.project_a}/asset")
        )

        outcome = pipeline.execute(scope, run.run_id)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        assert SqlProcessingArtifactRepository(session).list(scope, run_id=run.run_id) == []


def test_rerun_does_not_duplicate_artifacts(factory: sessionmaker[Session]) -> None:
    """同一 ``(run, stage, attempt)`` 重跑幂等：目录不出现重复行。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_a}/asset",),
            ),
        )
        artifact = StageArtifact(
            kind=ProcessingArtifactKind.METADATA,
            object_key=f"processing-run/{ids.project_a}/{run.run_id}/metadata/0000-mp4.json",
            schema_version="ddp.processing.metadata/1.0.0",
            content_type="application/json",
            size_bytes=512,
        )
        pipeline = _pipeline(
            session, _handlers_emitting(artifact, asset_key=f"raw/{ids.project_a}/asset")
        )

        pipeline.execute(scope, run.run_id)
        # 运行已成功，再次执行是 NOOP，不重复登记。
        second = pipeline.execute(scope, run.run_id)

        assert second.status == PipelineOutcomeStatus.NOOP.value
        stored = SqlProcessingArtifactRepository(session).list(scope, run_id=run.run_id)

    assert len(stored) == 1
