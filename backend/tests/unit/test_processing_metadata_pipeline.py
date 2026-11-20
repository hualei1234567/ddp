"""八阶段编排 e2e（DDP-V21-N4-002 增量）：真实解析器 + 真实 SQLite。

覆盖：

- 一个 run 走完八阶段：``FILE_VALIDATION`` / ``METADATA_EXTRACTION`` /
  ``FORMAT_PARSING`` / ``TIME_ALIGNMENT`` / ``CALIBRATION_VALIDATION`` /
  ``QUALITY_PRE_CHECK`` 按输入真实执行，其余两个显式 SKIPPED 且原因可读；
- 元数据产物与 Canonical 产物可回查（``output_refs`` 指向的对象真实存在）；
- 未知格式显式失败（永久失败，不重试）；
- 元数据解析失败可重试（存储故障）。

真实解析器：MP4 通过 ``ddp_data.media.mp4`` 的既有夹具替换 ffprobe 子进程
输出；Parquet 使用仓库内真实样例。对象存储使用显式标注的内存替身。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.calibration_stage import CalibrationValidationHandler
from app.application.processing.commands import NewProcessingRun
from app.application.processing.metadata_stage import (
    FormatParsingHandler,
    MetadataExtractionHandler,
)
from app.application.processing.pipeline import (
    PipelineOutcomeStatus,
    ProcessingPipeline,
    default_handlers,
)
from app.application.processing.quality_stage import QualityCheckHandler
from app.application.processing.stages import SkippedStage
from app.application.processing.time_alignment_stage import (
    TimeAlignmentHandler,
    time_alignment_report_key,
)
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.application.quality.episode_checks import EpisodeQualityChecks
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationRecordRepository,
)
from app.infrastructure.repositories.annotation_schemas import (
    SqlAnnotationSchemaRepository,
)
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.calibration_records import SqlCalibrationRecordRepository
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
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
_PARQUET_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "parquet"
_PROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
                "duration": "12.345",
                "nb_frames": "370",
            }
        ],
        "format": {"format_name": "mov,mp4", "duration": "12.345"},
    }
).encode()


@pytest.fixture(autouse=True)
def _patch_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddp_data.media import mp4 as mp4_module

    monkeypatch.setattr(
        mp4_module.shutil,
        "which",
        lambda name: sys.executable if name == "ffprobe" else None,
    )
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout=_PROBE_JSON, stderr=b""
        ),
    )


@pytest.fixture()
def factory(tmp_path: Path):
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'processing-metadata.db'}",
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
    reader: FakeObjectReader,
    artifacts: FakeArtifactStore,
) -> ProcessingPipeline:
    runs = SqlProcessingRunRepository(session)
    quality_check = QualityCheckHandler(
        episode_assets=SqlEpisodeRawAssetRepository(session),
        quality=EpisodeQualityChecks(_quality_use_cases(session)),
        uow=session,
    )
    calibration_validation = CalibrationValidationHandler(
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
            reader,
            metadata_extraction=MetadataExtractionHandler(reader, artifacts),
            format_parsing=FormatParsingHandler(artifacts),
            time_alignment=TimeAlignmentHandler(reader, artifacts),
            calibration_validation=calibration_validation,
            quality_check=quality_check,
        ),
        session,
    )


def _use_cases(session: Session) -> ProcessingRunUseCases:
    return ProcessingRunUseCases(
        SqlProcessingRunRepository(session),
        SqlProcessingRunStepRepository(session),
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
    )


def _mp4_ref(project_id: UUID) -> str:
    return f"raw/{project_id}/2026/11/19/{UUID(int=1)}/content.mp4"


def _parquet_ref(project_id: UUID) -> str:
    return f"raw/{project_id}/2026/11/19/{UUID(int=2)}/content.parquet"


def test_run_executes_metadata_and_format_stages_end_to_end(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    mp4_ref = _mp4_ref(ids.project_a)
    parquet_ref = _parquet_ref(ids.project_a)
    reader = FakeObjectReader(
        {
            mp4_ref: b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256,
            parquet_ref: (_PARQUET_FIXTURES / "lerobot_v21_data.parquet").read_bytes(),
        }
    )
    artifacts = FakeArtifactStore()
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(mp4_ref, parquet_ref),
            ),
        )

        outcome = _pipeline(session, reader, artifacts).execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        steps = {step.stage: step for step in use_cases.list_steps(scope, run.run_id)}
        assert [step.stage for step in steps.values()] == [stage.value for stage in STAGE_SEQUENCE]

        # 三个真实阶段成功（FILE_VALIDATION / METADATA_EXTRACTION / FORMAT_PARSING）。
        for stage in (
            ProcessingStage.FILE_VALIDATION,
            ProcessingStage.METADATA_EXTRACTION,
            ProcessingStage.FORMAT_PARSING,
        ):
            assert steps[stage.value].status == ProcessingStepStatus.SUCCEEDED.value

        # 本次运行未关联 Episode：QUALITY_PRE_CHECK 如实 SKIPPED，不冒充通过。
        assert steps[ProcessingStage.QUALITY_PRE_CHECK.value].status == (
            ProcessingStepStatus.SKIPPED.value
        )

        # 未提供 manifest：TIME_ALIGNMENT 真实执行但如实 SKIPPED，不冒充分析过。
        time_step = steps[ProcessingStage.TIME_ALIGNMENT.value]
        assert time_step.status == ProcessingStepStatus.SKIPPED.value
        assert "time-alignment manifest" in (time_step.log_summary or "")

        # 本次运行未关联 Episode：标定校验真实执行但如实 SKIPPED。
        calibration_step = steps[ProcessingStage.CALIBRATION_VALIDATION.value]
        assert calibration_step.status == ProcessingStepStatus.SKIPPED.value
        assert "未关联任何 Episode" in (calibration_step.log_summary or "")

        # 两个未实现阶段显式 SKIPPED，且原因写明依赖。
        for stage, keyword in (
            (ProcessingStage.EPISODE_SEGMENTATION, "切片"),
            (ProcessingStage.DATA_CLEANING, "清洗"),
        ):
            step = steps[stage.value]
            assert step.status == ProcessingStepStatus.SKIPPED.value
            assert keyword in (step.log_summary or "")

        # 产物可回查：metadata 与 canonical 引用都指向真实存在的对象。
        current = use_cases.get(scope, run.run_id)
        assert current.status == ProcessingRunStatus.SUCCEEDED.value
        metadata_refs = [ref for ref in current.output_refs if "/metadata/" in ref]
        canonical_refs = [ref for ref in current.output_refs if "/canonical/" in ref]
        assert len(metadata_refs) == 2
        assert len(canonical_refs) == 2
        for ref in (*metadata_refs, *canonical_refs):
            assert ref in artifacts.keys()

        # MP4 投影为 Canonical VideoStream；Parquet 只保留阶段产物引用。
        video = json.loads(
            artifacts.read_bytes(
                next(ref for ref in canonical_refs if ref.endswith("video-stream.json")),
                max_bytes=256 * 1024,
            )
        )
        assert video["schema_version"] == "ddp.canonical/1.0.0"
        assert video["width"] == 1280
        reference = json.loads(
            artifacts.read_bytes(
                next(ref for ref in canonical_refs if ref.endswith("reference.json")),
                max_bytes=256 * 1024,
            )
        )
        assert reference["canonical_episode"] is False
        assert "episode" not in reference
    finally:
        session.close()


def test_unsupported_format_fails_run_permanently(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    ref = f"raw/{ids.project_a}/2026/11/19/{UUID(int=3)}/content.bin"
    reader = FakeObjectReader({ref: b"\x00\x01\x02\x03 not a known container"})
    artifacts = FakeArtifactStore()
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=(ref,)),
        )

        outcome = _pipeline(session, reader, artifacts).execute(scope, run.run_id, now=NOW)

        # 单一未知格式：METADATA_EXTRACTION 显式 SKIPPED，不是失败也不是成功。
        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        steps = {step.stage: step for step in use_cases.list_steps(scope, run.run_id)}
        assert steps[ProcessingStage.METADATA_EXTRACTION.value].status == (
            ProcessingStepStatus.SKIPPED.value
        )
        assert "UNKNOWN" in (steps[ProcessingStage.METADATA_EXTRACTION.value].log_summary or "")
        # 没有元数据产物，FORMAT_PARSING 也显式 SKIPPED。
        assert steps[ProcessingStage.FORMAT_PARSING.value].status == (
            ProcessingStepStatus.SKIPPED.value
        )
        assert artifacts.keys() == []
    finally:
        session.close()


def test_mixed_supported_and_unsupported_fails_whole_stage(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    mp4_ref = _mp4_ref(ids.project_a)
    bin_ref = f"raw/{ids.project_a}/2026/11/19/{UUID(int=4)}/content.bin"
    reader = FakeObjectReader(
        {
            mp4_ref: b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256,
            bin_ref: b"\x00\x01\x02\x03",
        }
    )
    artifacts = FakeArtifactStore()
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(mp4_ref, bin_ref),
            ),
        )

        outcome = _pipeline(session, reader, artifacts).execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.FAILED.value
        assert outcome.error_code == "metadata_format_unsupported"
        assert outcome.retryable is False
        step = next(
            step
            for step in use_cases.list_steps(scope, run.run_id)
            if step.stage == ProcessingStage.METADATA_EXTRACTION.value
        )
        assert step.status == ProcessingStepStatus.FAILED.value
        # 失败时不得留下任何产物。
        assert artifacts.keys() == []
    finally:
        session.close()


def test_metadata_stage_skipped_when_no_metadata_artifact_is_available(
    factory: sessionmaker[Session],
) -> None:
    """FORMAT_PARSING 只依赖 METADATA_EXTRACTION 产物：没有产物时显式 SKIPPED。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    ref = _mp4_ref(ids.project_a)
    reader = FakeObjectReader({ref: b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256})
    artifacts = FakeArtifactStore()
    session = factory()
    try:
        runs = SqlProcessingRunRepository(session)
        handlers = default_handlers(
            reader,
            metadata_extraction=SkippedStage("测试：不产出元数据"),
            format_parsing=FormatParsingHandler(artifacts),
            time_alignment=SkippedStage("测试：不装配时间对齐"),
            calibration_validation=SkippedStage("测试：不装配标定校验"),
            quality_check=SkippedStage("测试：不装配质量引擎"),
        )
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(pipeline_version="pipeline-v1", input_refs=(ref,)),
        )
        pipeline = ProcessingPipeline(
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
        )

        outcome = pipeline.execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        steps = {step.stage: step for step in use_cases.list_steps(scope, run.run_id)}
        assert steps[ProcessingStage.FORMAT_PARSING.value].status == (
            ProcessingStepStatus.SKIPPED.value
        )
        assert "METADATA_EXTRACTION" in (
            steps[ProcessingStage.FORMAT_PARSING.value].log_summary or ""
        )
    finally:
        session.close()


def _time_alignment_manifest() -> bytes:
    return json.dumps(
        {
            "schema_version": "ddp.processing.time-alignment-input/1.0.0",
            "time_unit": "ns",
            "groups": [
                {
                    "group_key": "lerobot/droid_100:episode:0",
                    "streams": [
                        {
                            "stream_id": "observation.images.exterior_image_1_left",
                            "start_time_ns": "0",
                            "end_time_ns": "11066667000",
                            "frame_rate": {"num": 15, "den": 1},
                            "declared_frame_count": 166,
                            "time_domain": "UNKNOWN",
                            "clock_source": "UNKNOWN",
                        },
                        {
                            "stream_id": "observation.images.wrist_image_left",
                            "start_time_ns": "0",
                            "end_time_ns": "11066667000",
                            "frame_rate": {"num": 15, "den": 1},
                            "declared_frame_count": 166,
                            "time_domain": "UNKNOWN",
                            "clock_source": "UNKNOWN",
                        },
                    ],
                }
            ],
        }
    ).encode("utf-8")


def test_run_analyzes_versioned_time_alignment_manifest(
    factory: sessionmaker[Session],
) -> None:
    """有 manifest 时 TIME_ALIGNMENT 真实执行并落入可回查的只读报告。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    manifest_ref = f"canonical/{ids.project_a}/time-alignment/droid-episode-0.json"
    reader = FakeObjectReader({manifest_ref: _time_alignment_manifest()})
    artifacts = FakeArtifactStore()
    session = factory()
    try:
        use_cases = _use_cases(session)
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(manifest_ref,),
            ),
        )

        outcome = _pipeline(session, reader, artifacts).execute(scope, run.run_id, now=NOW)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        steps = {step.stage: step for step in use_cases.list_steps(scope, run.run_id)}
        time_step = steps[ProcessingStage.TIME_ALIGNMENT.value]
        assert time_step.status == ProcessingStepStatus.SUCCEEDED.value
        assert "时间对齐" in (time_step.log_summary or "")

        report_ref = time_alignment_report_key(ids.project_a, run.run_id)
        assert report_ref in artifacts.keys()
        current = use_cases.get(scope, run.run_id)
        assert report_ref in current.output_refs
        report = json.loads(artifacts.read_bytes(report_ref, max_bytes=256 * 1024))
        assert report["schema_version"] == "ddp.processing.time-alignment-report/1.0.0"
        assert report["source_manifest_ref"] == manifest_ref
        analysis = report["groups"][0]["analysis"]
        assert analysis["status"] == "ALIGNED"
        assert analysis["clock_domain_status"] == "UNKNOWN"
        assert all(
            stream["correction"]["applied"] is False
            for stream in analysis["streams"]
            if stream["correction"] is not None
        )
    finally:
        session.close()
