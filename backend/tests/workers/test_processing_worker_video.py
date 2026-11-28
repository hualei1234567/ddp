"""Worker 真实装配的视频链端到端：MP4 资产 → video_frames + 归一化（V6.1）。

与 ``test_processing_worker_segmentation`` 同一策略：验证 **Worker 的真实
装配**（``_build_pipeline``），不预置任何帧行——少了 ``VideoDecodeHandler``
或 ``TimestampNormalizeHandler`` 的接线，断言会因"库里没有帧"而失败。

被替换的只有 ffprobe 子进程（CI 不装 FFmpeg 的既有做法）——替换的是
外部工具输出，不是本仓库的处理逻辑。

钉住的历史缺陷（MEM-INC-027 同类）：处理器与适配器都有实现、都有单测，
但组合根从未装配——「有能力无接线」，生产里视频资产永远被 SKIPPED，
看起来像"没有需要解码的视频"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import ProcessingRunUseCases
from app.core.config import Settings
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.models import RawAsset
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.video_frame import VideoFrameModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.workers.processing import ProcessingRunWorker, RunProcessingStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

#: 3 帧视频的 ffprobe 逐帧输出（``-show_entries frame=media_type,pts_time``）。
_FRAME_PROBE_JSON = json.dumps(
    {
        "frames": [
            {"media_type": "video", "pts_time": "0.000000"},
            {"media_type": "video", "pts_time": "0.100000"},
            {"media_type": "video", "pts_time": "0.200000"},
        ]
    }
).encode()


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture()
def env(tmp_path: Path):
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'video-chain.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = seed_identity(factory)
    try:
        yield factory, ids
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _resolve_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddp_data.media import video_decode as probe_module

    monkeypatch.setattr(
        probe_module.shutil,
        "which",
        lambda name: sys.executable if name == "ffprobe" else None,
    )
    monkeypatch.setattr(
        probe_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout=_FRAME_PROBE_JSON, stderr=b""
        ),
    )


def _worker(factory: sessionmaker[Session], objects: dict[str, bytes]) -> ProcessingRunWorker:
    reader = FakeObjectReader(objects)
    return ProcessingRunWorker(
        factory,
        Settings(  # type: ignore[call-arg]
            validation_max_attempts=2,
            validation_retry_base_seconds=1.0,
            validation_retry_max_seconds=2.0,
        ),
        inspector=reader,
        reader=reader,
        artifacts=FakeArtifactStore(),
    )


def _seed_video_asset(factory: sessionmaker[Session], ids) -> RawAsset:
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    asset_id = uuid4()
    with factory() as session:
        repo = SqlRawAssetRepository(session)
        asset = repo.add(
            scope,
            RawAsset(
                asset_id=asset_id,
                project_id=scope.project_id,
                code=f"RAW-{asset_id.hex[:20]}",
                source_type=RawAssetSourceType.IMPORT,
                original_filename="content.mp4",
                object_key=f"raw/{scope.project_id}/2026/11/28/{asset_id}/content.mp4",
                size_bytes=1024,
                content_type="video/mp4",
                format=RawAssetFormat.MP4,
                sha256="sha256:" + "c" * 64,
                status=RawAssetStatus.VALIDATING,
                uploaded_by=scope.actor_id,
                ingested_at=_now(),
                created_at=_now(),
                updated_at=_now(),
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            ),
        )
        repo.save(scope, asset.mark_valid(now=_now()), expected_version=asset.version)
        session.commit()
    return asset


def _create_run(factory: sessionmaker[Session], ids, *, asset: RawAsset) -> str:
    with factory() as session:
        use_cases = ProcessingRunUseCases(
            SqlProcessingRunRepository(session),
            SqlProcessingRunStepRepository(session),
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            raw_assets=SqlRawAssetRepository(session),
        )
        run = use_cases.create(
            ProjectScope(ids.project_a, actor_id=ids.editor_a),
            NewProcessingRun(pipeline_version="pipeline-v1", raw_asset_id=asset.asset_id),
        )
        return run.run_id


def test_video_asset_decodes_to_frames_and_normalized_time(env) -> None:
    """视频资产 → VIDEO_DECODE/FRAME_INDEX 落帧 → 归一化填列，全链 SUCCEEDED。"""
    factory, ids = env
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    asset = _seed_video_asset(factory, ids)
    run_id = _create_run(factory, ids, asset=asset)
    worker = _worker(factory, {asset.object_key: b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64})

    result = worker.process_once(now=_now())

    assert result["status"] == RunProcessingStatus.PUBLISHED.value, result
    with factory() as session:
        frames = session.scalars(select(VideoFrameModel)).all()
        assert len(frames) == 3
        by_index = {frame.frame_index: frame for frame in frames}
        # 原始时间原样落库（VIDEO_DECODE 红线：不归一化）
        first = by_index[0]
        assert first.pts_seconds == Decimal("0.000000")
        assert first.timestamp_raw == "0.000000"
        assert first.decode_status == "OK"
        assert first.asset_id == asset.asset_id
        assert first.parser_version.startswith("ddp.video_decode/")
        # 归一化列由 TIMESTAMP_NORMALIZE 填写，不再是 NULL
        assert first.timestamp_normalized_seconds == Decimal("0.000000000")
        assert first.normalize_method == "SECONDS_IDENTITY"
        second = by_index[1]
        assert second.timestamp_normalized_seconds == Decimal("0.100000000")

    with factory() as session:
        steps = SqlProcessingRunStepRepository(session).list(scope, run_id=run_id, limit=100)
    by_stage = {step.stage: step for step in steps}
    decode_step = by_stage[ProcessingStage.VIDEO_DECODE.value]
    assert decode_step.status == ProcessingStepStatus.SUCCEEDED.value
    assert "已解码" in (decode_step.log_summary or "")
    normalize_step = by_stage[ProcessingStage.TIMESTAMP_NORMALIZE.value]
    assert normalize_step.status == ProcessingStepStatus.SUCCEEDED.value
    assert "已归一化 3 行" in (normalize_step.log_summary or "")


def test_non_video_asset_skips_video_stages(env) -> None:
    """非视频资产：三个视频阶段显式 SKIPPED，运行整体仍 SUCCEEDED。"""
    factory, ids = env
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    asset_id = uuid4()
    with factory() as session:
        repo = SqlRawAssetRepository(session)
        asset = repo.add(
            scope,
            RawAsset(
                asset_id=asset_id,
                project_id=scope.project_id,
                code=f"RAW-{asset_id.hex[:20]}",
                source_type=RawAssetSourceType.IMPORT,
                original_filename="content.json",
                object_key=f"raw/{scope.project_id}/2026/11/28/{asset_id}/content.json",
                size_bytes=64,
                content_type="application/json",
                format=RawAssetFormat.JSON,
                sha256="sha256:" + "d" * 64,
                status=RawAssetStatus.VALIDATING,
                uploaded_by=scope.actor_id,
                ingested_at=_now(),
                created_at=_now(),
                updated_at=_now(),
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            ),
        )
        repo.save(scope, asset.mark_valid(now=_now()), expected_version=asset.version)
        session.commit()
    run_id = _create_run(factory, ids, asset=asset)
    worker = _worker(factory, {asset.object_key: b"{}"})

    result = worker.process_once(now=_now())

    assert result["status"] == RunProcessingStatus.PUBLISHED.value, result
    with factory() as session:
        assert session.scalars(select(VideoFrameModel)).all() == []
        steps = SqlProcessingRunStepRepository(session).list(scope, run_id=run_id, limit=100)
    by_stage = {step.stage: step for step in steps}
    for stage in (
        ProcessingStage.VIDEO_DECODE,
        ProcessingStage.FRAME_INDEX,
        ProcessingStage.TIMESTAMP_NORMALIZE,
    ):
        step = by_stage[stage.value]
        assert step.status == ProcessingStepStatus.SKIPPED.value, stage
