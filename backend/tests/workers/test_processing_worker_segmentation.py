"""Worker 端到端：单文件 MP4 → Episode（V31-018）。

与 ``test_processing_worker`` 共享的内存替身不同，本文件验证的是 **Worker 的真实
装配**：``_build_pipeline`` 把元数据产物读取器与 Episode 创建器接到分段阶段，
元数据阶段真实写产物、真实登记产物目录。这里不预置任何产物或 Episode：少了
任何一处接线，断言都会因为"库里没有 Episode"而失败。

唯一被替换的是 ffprobe 子进程（CI 不装 FFmpeg 的既有做法），替换的是**外部
工具输出**，不是本仓库的处理逻辑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import ProcessingRunUseCases
from app.core.config import Settings
from app.domain.episodes.segmentation_rules import EPISODE_BOUNDARY_RULE_VERSION
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.models import RawAsset
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.processing.episode_creation import stable_episode_code
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
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.workers.processing import ProcessingRunWorker, RunProcessingStatus
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

_PROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
                "duration": "5.000",
                "nb_frames": "150",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "5.000",
            "size": "280",
        },
    }
).encode()


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture()
def env(tmp_path: Path):
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'processing.db'}",
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


def _seed_valid_mp4_asset(factory: sessionmaker[Session], ids) -> RawAsset:
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
                object_key=f"raw/{scope.project_id}/2026/11/22/{asset_id}/content.mp4",
                size_bytes=280,
                content_type="application/octet-stream",
                format=RawAssetFormat.MP4,
                sha256="sha256:" + "b" * 64,
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


def _create_run(
    factory: sessionmaker[Session],
    ids,
    *,
    asset: RawAsset,
) -> str:
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


def test_worker_creates_episode_from_single_file_mp4(env) -> None:
    factory, ids = env
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    asset = _seed_valid_mp4_asset(factory, ids)
    run_id = _create_run(factory, ids, asset=asset)
    worker = _worker(
        factory,
        {asset.object_key: b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256},
    )

    result = worker.process_once(now=_now())

    assert result["status"] == RunProcessingStatus.PUBLISHED.value, result
    with factory() as session:
        episode = SqlEpisodeRepository(session).get_by_code(
            scope, stable_episode_code(asset.asset_id, 0)
        )
        assert episode is not None
        assert (episode.start_time_ns, episode.end_time_ns) == (0, 5_000_000_000)
        assert episode.segmentation_rule_version == EPISODE_BOUNDARY_RULE_VERSION
        links = SqlEpisodeRawAssetRepository(session).list_for_asset(scope, asset.asset_id)
        assert [link.episode_id for link in links] == [episode.episode_id]
    with factory() as session:
        steps = SqlProcessingRunStepRepository(session).list(scope, run_id=run_id, limit=100)
    segmentation = next(
        step for step in steps if step.stage == ProcessingStage.EPISODE_SEGMENTATION.value
    )
    assert segmentation.status == ProcessingStepStatus.SUCCEEDED.value
    assert "新建 1 个" in (segmentation.log_summary or "")
