"""EPISODE_SEGMENTATION 在明确边界下创建 Episode 的流水线集成（V31-018，真实 SQLite）。

装配方式与 Worker 相同：真实 ``EpisodeService``（经组合根）、真实产物目录仓储、
真实 Episode 仓储，只有对象存储用一个按 key 返回 JSON 的替身——本文件要验证
的是"边界读取 → 建 Episode 落库 → 重跑幂等 → 无边界不伪造"，不是对象存储本身。

覆盖：

- 单文件 MP4 输入（元数据产物给出 duration）→ 创建 Episode，边界、规则版本、
  pipeline 标识与 raw asset 关联都正确；
- 同一资产第二次运行 → 报告"已存在"，不产生第二条 Episode；
- 多 Episode 容器（MCAP）→ 阶段 SKIPPED 并写明原因，库里没有 Episode。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.calibration_stage import CalibrationValidationHandler
from app.application.processing.commands import NewProcessingRun
from app.application.processing.pipeline import (
    PipelineOutcomeStatus,
    ProcessingPipeline,
    SkippedStage,
    default_handlers,
)
from app.application.processing.segmentation_stage import EpisodeSegmentationHandler
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.composition.episodes import build_episode_service
from app.domain.episodes.segmentation_rules import (
    EPISODE_BOUNDARY_RULE_VERSION,
    build_segmentation_rule_registry,
)
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.models import RawAsset
from app.domain.ingestion.validation import FORMAT_EXTENSIONS
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.processing.episode_creation import (
    PROCESSING_PIPELINE_ID,
    ProcessingEpisodeCreator,
    stable_episode_code,
)
from app.infrastructure.processing.metadata_documents import (
    ProcessingMetadataDocumentReader,
)
from app.infrastructure.repositories.calibration_records import SqlCalibrationRecordRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.processing_artifacts import (
    SqlProcessingArtifactRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from ddp_data.canonical.v1 import SCHEMA_VERSION
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
MAX_DOCUMENT_BYTES = 1_000_000


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


class _ObjectStore:
    """只读替身：按产物 key 返回预置字节；写入不属于本用例。"""

    def __init__(self, objects: Mapping[str, bytes]) -> None:
        self._objects = dict(objects)

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        data = self._objects[object_key]
        assert len(data) <= max_bytes
        return data

    def put_bytes(self, object_key: str, data: bytes) -> str:  # pragma: no cover
        raise AssertionError(f"本用例不写产物：{object_key}")


def _scope(ids) -> ProjectScope:  # type: ignore[no-untyped-def]
    return ProjectScope(ids.project_a, actor_id=ids.editor_a)


def _seed_raw_asset(
    factory: sessionmaker[Session],
    ids,  # type: ignore[no-untyped-def]
    raw_format: RawAssetFormat,
) -> RawAsset:
    """落一条 VALID 的原始资产；key 由既有生成器产生，不手写格式。"""
    scope = _scope(ids)
    asset_id = uuid4()
    extension = FORMAT_EXTENSIONS[raw_format]
    with factory() as session:
        repo = SqlRawAssetRepository(session)
        asset = repo.add(
            scope,
            RawAsset(
                asset_id=asset_id,
                project_id=scope.project_id,
                code=f"RAW-{asset_id.hex[:20]}",
                source_type=RawAssetSourceType.IMPORT,
                original_filename=f"content.{extension}",
                object_key=(f"raw/{scope.project_id}/2026/11/22/{asset_id}/content.{extension}"),
                size_bytes=4096,
                content_type="application/octet-stream",
                format=raw_format,
                sha256="sha256:" + "a" * 64,
                status=RawAssetStatus.VALIDATING,
                uploaded_by=scope.actor_id,
                ingested_at=NOW,
                created_at=NOW,
                updated_at=NOW,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            ),
        )
        repo.save(scope, asset.mark_valid(now=NOW), expected_version=asset.version)
        session.commit()
    return asset


def _register_metadata_artifact(
    session: Session,
    scope: ProjectScope,
    *,
    run_id: UUID,
    source_ref: str,
    raw_format: RawAssetFormat,
    metadata: Mapping[str, object],
) -> tuple[str, bytes]:
    """登记一条 METADATA 产物（与元数据阶段产出的形状一致）。"""
    object_key = f"processing/{scope.project_id}/{run_id}/metadata/0.json"
    document = {
        "schema_version": "ddp.processing.metadata/1.0.0",
        "source_ref": source_ref,
        "format": raw_format.value,
        "adapter_id": "test",
        "adapter_version": "1.0.0",
        "object_size_bytes": 4096,
        "metadata": dict(metadata),
    }
    data = json.dumps(document).encode()
    SqlProcessingArtifactRepository(session).add(
        scope,
        ProcessingArtifact(
            artifact_id=uuid4(),
            project_id=scope.project_id,
            run_id=run_id,
            stage=ProcessingStage.METADATA_EXTRACTION.value,
            attempt=1,
            kind=ProcessingArtifactKind.METADATA.value,
            object_key=object_key,
            schema_version="ddp.processing.metadata/1.0.0",
            content_type="application/json",
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            source_ref=source_ref,
            created_at=NOW,
            created_by=scope.actor_id,
        ),
    )
    session.commit()
    return object_key, data


def _use_cases(session: Session) -> ProcessingRunUseCases:
    return ProcessingRunUseCases(
        SqlProcessingRunRepository(session),
        SqlProcessingRunStepRepository(session),
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        raw_assets=SqlRawAssetRepository(session),
    )


def _pipeline(
    session: Session, *, inspector: _Inspector, store: _ObjectStore
) -> ProcessingPipeline:
    """与 Worker 相同的装配：真实读取器 + 真实创建器。"""
    runs = SqlProcessingRunRepository(session)
    segmentation = EpisodeSegmentationHandler(
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        rules=build_segmentation_rule_registry(),
        metadata_reader=ProcessingMetadataDocumentReader(
            SqlProcessingArtifactRepository(session),
            store,
            max_document_bytes=MAX_DOCUMENT_BYTES,
        ),
        creator=ProcessingEpisodeCreator(
            service=build_episode_service(session, with_idempotency=False),
            episodes=SqlEpisodeRepository(session),
            canonical_schema_version=SCHEMA_VERSION,
        ),
    )
    calibration = CalibrationValidationHandler(
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        calibrations=SqlCalibrationRecordRepository(session),
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
            metadata_extraction=SkippedStage("集成测试预置元数据产物，不重新探测"),
            format_parsing=SkippedStage("集成测试不装配格式解析"),
            time_alignment=SkippedStage("集成测试不装配时间对齐"),
            calibration_validation=calibration,
            episode_segmentation=segmentation,
            quality_check=SkippedStage("集成测试不装配自动质检"),
        ),
        session,
        artifacts=SqlProcessingArtifactRepository(session),
    )


def _segmentation_step(use_cases: ProcessingRunUseCases, scope: ProjectScope, run_id: UUID):
    return next(
        step
        for step in use_cases.list_steps(scope, run_id)
        if step.stage == ProcessingStage.EPISODE_SEGMENTATION.value
    )


def test_mp4_input_with_known_duration_creates_episode(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = _scope(ids)
    asset = _seed_raw_asset(factory, ids, RawAssetFormat.MP4)
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", raw_asset_id=asset.asset_id),
        )
        object_key, data = _register_metadata_artifact(
            session,
            scope,
            run_id=run.run_id,
            source_ref=asset.object_key,
            raw_format=RawAssetFormat.MP4,
            metadata={"duration_ms": 5000},
        )

        outcome = _pipeline(
            session,
            inspector=_Inspector({asset.object_key: 4096}),
            store=_ObjectStore({object_key: data}),
        ).execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        step = _segmentation_step(use_cases, scope, run.run_id)
        assert step.status == ProcessingStepStatus.SUCCEEDED.value
        assert "新建 1 个" in (step.log_summary or "")
        assert "已存在 0 个" in (step.log_summary or "")

        episodes = SqlEpisodeRepository(session)
        episode = episodes.get_by_code(scope, stable_episode_code(asset.asset_id, 0))
        assert episode is not None
        assert episode.start_time_ns == 0
        assert episode.end_time_ns == 5_000_000_000
        assert episode.segmentation_rule_version == EPISODE_BOUNDARY_RULE_VERSION
        assert episode.canonical_schema_version == SCHEMA_VERSION
        assert episode.pipeline_id == PROCESSING_PIPELINE_ID
        assert episode.pipeline_version == "pipeline-v1"
        assert episode.created_by == scope.actor_id

        links = SqlEpisodeRawAssetRepository(session).list_for_asset(scope, asset.asset_id)
        assert [link.episode_id for link in links] == [episode.episode_id]
        assert links[0].start_time_ns == 0
        assert links[0].end_time_ns == 5_000_000_000
    finally:
        session.close()


def test_second_run_reports_existing_episode_without_duplicating(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = _scope(ids)
    asset = _seed_raw_asset(factory, ids, RawAssetFormat.MP4)

    for expected_new, expected_existing in ((1, 0), (0, 1)):
        session = factory()
        try:
            use_cases = _use_cases(session)
            run = use_cases.create(
                scope,
                NewProcessingRun(pipeline_version="pipeline-v1", raw_asset_id=asset.asset_id),
            )
            object_key, data = _register_metadata_artifact(
                session,
                scope,
                run_id=run.run_id,
                source_ref=asset.object_key,
                raw_format=RawAssetFormat.MP4,
                metadata={"duration_ms": 5000},
            )

            outcome = _pipeline(
                session,
                inspector=_Inspector({asset.object_key: 4096}),
                store=_ObjectStore({object_key: data}),
            ).execute(scope, run.run_id, now=NOW)

            assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
            summary = _segmentation_step(use_cases, scope, run.run_id).log_summary or ""
            assert f"新建 {expected_new} 个" in summary
            assert f"已存在 {expected_existing} 个" in summary
        finally:
            session.close()

    with factory() as session:
        episodes = SqlEpisodeRepository(session).list(scope, limit=50)
        assert [episode.code for episode in episodes] == [stable_episode_code(asset.asset_id, 0)]


def test_container_without_boundary_is_skipped_and_creates_nothing(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = _scope(ids)
    asset = _seed_raw_asset(factory, ids, RawAssetFormat.MCAP)
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", raw_asset_id=asset.asset_id),
        )
        object_key, data = _register_metadata_artifact(
            session,
            scope,
            run_id=run.run_id,
            source_ref=asset.object_key,
            raw_format=RawAssetFormat.MCAP,
            metadata={},
        )

        outcome = _pipeline(
            session,
            inspector=_Inspector({asset.object_key: 4096}),
            store=_ObjectStore({object_key: data}),
        ).execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        step = _segmentation_step(use_cases, scope, run.run_id)
        assert step.status == ProcessingStepStatus.SKIPPED.value
        assert "未创建 Episode" in (step.log_summary or "")
        assert "MCAP" in (step.log_summary or "")
        assert SqlEpisodeRepository(session).list(scope, limit=50) == []
    finally:
        session.close()
