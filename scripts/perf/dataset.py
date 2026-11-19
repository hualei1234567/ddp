"""临时数据库生命周期与固定规模合成数据。

设计约束：

- 每个数据规模一个**独立临时库**（``ddp_perf_*``），跑完即删；绝不写开发库；
- 建库前由 :mod:`perf.core` 校验 DSN 必须是本地白名单测试库；
- 数据生成只走 ORM 模型/表定义（复用现有 schema），不手写建表 SQL；
- 合成数据全部可识别（``PERF-*`` code / ``perf-baseline/`` 对象前缀），
  清理失败时也能按前缀找回。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, func, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from perf.core import ensure_safe_perf_db_name

BATCH_SIZE = 1000
SHA256_ZERO = "sha256:" + "0" * 64
_PIPELINE_ID = "perf-baseline"
_PIPELINE_VERSION = "1"
_CANONICAL_SCHEMA = "canonical.v1"
_SEGMENTATION_RULE = "perf-baseline.v1"
# 领域校验要求四项齐全且同步已校验；列表读取会重新构造领域对象，缺项直接 422。
_TIME_BASE = {
    "time_domain": "UTC",
    "clock_source": "HOST_CLOCK",
    "sync_method": "NTP",
    "sync_status": "VERIFIED",
}


@dataclass(slots=True)
class SeedResult:
    project_id: UUID
    user_id: UUID
    episode_ids: list[UUID] = field(default_factory=list)
    asset_ids: list[UUID] = field(default_factory=list)
    dataset_ids: list[UUID] = field(default_factory=list)
    data_source_id: UUID | None = None
    import_id: UUID | None = None
    media_asset_ids: list[UUID] = field(default_factory=list)
    object_keys: list[str] = field(default_factory=list)


def _admin_engine(base_dsn: str) -> Engine:
    # CREATE/DROP DATABASE 不能在事务块里执行：AUTOCOMMIT + 连接池关闭。
    return create_engine(base_dsn, isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def create_database(base_dsn: str, name: str) -> None:
    ensure_safe_perf_db_name(name)
    engine = _admin_engine(base_dsn)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def drop_database(base_dsn: str, name: str) -> None:
    """强制断开残留会话后删库；名字再次校验，避免误删非基线库。"""
    ensure_safe_perf_db_name(name)
    engine = _admin_engine(base_dsn)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


def run_migrations(dsn: str, backend_dir: Path) -> float:
    """在临时库执行 Alembic 到 head；复用真实迁移链，保证基线 schema 与线上一致。"""
    env = dict(os.environ)
    env["DDP_DATABASE_URL"] = dsn
    env["DDP_ENV"] = "local"
    started = time.perf_counter()
    completed = subprocess.run(  # noqa: S603 - 固定参数，不接受外部输入
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout + completed.stderr).splitlines()[-30:])
        raise RuntimeError(f"临时库迁移失败（exit={completed.returncode}）：\n{tail}")
    return elapsed


def seed_synthetic_data(
    dsn: str,
    *,
    scale: int,
    run_id: str,
    with_video: bool,
) -> SeedResult:
    """写入 scale 条 Episode / RawAsset / Dataset，全部带 PERF 前缀。

    只使用真实 ORM 模型的列定义：新增 NOT NULL 列时这里会直接失败，
    逼迫基线脚本跟随 schema 演进，而不是悄悄生成不完整的数据。
    """
    from app.infrastructure.db.models.datasets import DatasetModel
    from app.infrastructure.db.models.episodes import EpisodeModel
    from app.infrastructure.db.models.ingestion import RawAssetModel

    engine = create_engine(dsn, pool_pre_ping=True)
    result = SeedResult(project_id=uuid.uuid4(), user_id=uuid.uuid4())
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            _seed_identity(session, result, run_id=run_id)
            episodes: list[dict[str, Any]] = []
            assets: list[dict[str, Any]] = []
            datasets: list[dict[str, Any]] = []
            for index in range(scale):
                episode = _episode_row(index, project_id=result.project_id, user_id=result.user_id)
                asset = _asset_row(
                    index,
                    project_id=result.project_id,
                    user_id=result.user_id,
                    now=now,
                )
                dataset = _dataset_row(index, project_id=result.project_id, user_id=result.user_id)
                result.episode_ids.append(episode["id"])
                result.asset_ids.append(asset["id"])
                result.dataset_ids.append(dataset["id"])
                episodes.append(episode)
                assets.append(asset)
                datasets.append(dataset)
                if len(episodes) >= BATCH_SIZE:
                    _flush(
                        session,
                        EpisodeModel,
                        episodes,
                        RawAssetModel,
                        assets,
                        DatasetModel,
                        datasets,
                    )

            _flush(session, EpisodeModel, episodes, RawAssetModel, assets, DatasetModel, datasets)
            _finish_state_transitions(session, result, now=now)
            if with_video:
                _seed_video_metadata(session, result, run_id=run_id, now=now)
            session.commit()
        # 造数后重建统计信息：否则规划器按默认估计选计划，规模间不可比。
        with engine.begin() as connection:
            connection.execute(text("ANALYZE episodes, raw_assets, datasets"))
    finally:
        engine.dispose()
    return result


def _seed_identity(session: Session, result: SeedResult, *, run_id: str) -> None:
    from app.infrastructure.db.models.identity import Project, ProjectMember, User

    session.execute(
        insert(Project),
        [{"id": result.project_id, "code": f"PERF-{run_id}", "name": run_id}],
    )
    session.execute(
        insert(User),
        [{"id": result.user_id, "display_name": f"perf-{run_id}"}],
    )
    session.execute(
        insert(ProjectMember),
        [
            {
                "id": uuid.uuid4(),
                "project_id": result.project_id,
                "user_id": result.user_id,
                "role": "owner",
            }
        ],
    )
    session.flush()


def _episode_row(index: int, *, project_id: UUID, user_id: UUID) -> dict[str, Any]:
    start_ns = 1_700_000_000_000_000_000 + index * 1_000_000_000
    return {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "episode_lineage_id": uuid.uuid4(),
        "episode_version": 1,
        "code": f"PERF-EP-{index:06d}",
        "status": "PENDING",
        # 一半 PASS / 一半 WARN，供 quality_status 过滤场景使用。
        "quality_status": "PASS" if index % 2 == 0 else "WARN",
        "scene_labels": ["perf", "indoor" if index % 3 else "outdoor"],
        "start_time_ns": start_ns,
        "end_time_ns": start_ns + 1_000_000_000,
        "duration_ns": 1_000_000_000,
        "time_base": _TIME_BASE,
        "canonical_schema_version": _CANONICAL_SCHEMA,
        "pipeline_id": _PIPELINE_ID,
        "pipeline_version": _PIPELINE_VERSION,
        "segmentation_rule_version": _SEGMENTATION_RULE,
        "frame_count": 300,
        "created_by": user_id,
        "updated_by": user_id,
    }


def _asset_row(index: int, *, project_id: UUID, user_id: UUID, now: datetime) -> dict[str, Any]:
    asset_id = uuid.uuid4()
    return {
        "id": asset_id,
        "project_id": project_id,
        "code": f"PERF-RA-{index:06d}",
        "source_type": "UPLOAD",
        "original_filename": f"perf-{index:06d}.mp4",
        # 原始内容 Key 有固定结构：raw/<project>/<y>/<m>/<d>/<asset>/content.ext。
        "object_key": f"raw/{project_id}/2026/11/19/{asset_id}/content.mp4",
        "size_bytes": 1024,
        "content_type": "video/mp4",
        "format": "MP4",
        # 每行 Hash 唯一：库内 (project, sha256, size) 活跃唯一约束
        # 会把内容完全相同的合成资产判为重复。
        "sha256": f"sha256:{index:064x}",
        # 库内守卫只允许以 VALIDATING 建行，下面再走合法转移。
        "status": "VALIDATING",
        "captured_at": now,
        "ingested_at": now,
        "storage_class": "STANDARD",
        "uploaded_by": user_id,
        "created_by": user_id,
        "updated_by": user_id,
    }


def _dataset_row(index: int, *, project_id: UUID, user_id: UUID) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "dataset_lineage_id": uuid.uuid4(),
        "code": f"PERF-DS-{index:06d}",
        "name": f"perf-dataset-{index:06d}",
        "purpose": "TRAINING",
        "owner_id": user_id,
        # 库内守卫只允许以 DRAFT 建行，随后走 DRAFT→ACTIVE。
        "status": "DRAFT",
        "tags": ["perf"],
        "usage_policy": "TRAINING_ALLOWED",
        "created_by": user_id,
        "updated_by": user_id,
    }


def _finish_state_transitions(session: Session, result: SeedResult, *, now: datetime) -> None:
    from app.infrastructure.db.models.datasets import DatasetModel
    from app.infrastructure.db.models.ingestion import RawAssetModel

    # VALIDATING → VALID、DRAFT → ACTIVE 都是状态机允许的转移；不绕过库内守卫。
    session.execute(
        update(RawAssetModel)
        .where(
            RawAssetModel.project_id == result.project_id,
            RawAssetModel.status == "VALIDATING",
        )
        .values(status="VALID", verified_at=now, updated_by=result.user_id)
    )
    session.execute(
        update(DatasetModel)
        .where(
            DatasetModel.project_id == result.project_id,
            DatasetModel.status == "DRAFT",
        )
        .values(status="ACTIVE", updated_by=result.user_id)
    )


def _flush(
    session: Session,
    episode_model: Any,
    episodes: list[dict[str, Any]],
    asset_model: Any,
    assets: list[dict[str, Any]],
    dataset_model: Any,
    datasets: list[dict[str, Any]],
) -> None:
    if episodes:
        session.execute(insert(episode_model), episodes)
        episodes.clear()
    if assets:
        session.execute(insert(asset_model), assets)
        assets.clear()
    if datasets:
        session.execute(insert(dataset_model), datasets)
        datasets.clear()
    session.flush()


def _seed_video_metadata(
    session: Session,
    result: SeedResult,
    *,
    run_id: str,
    now: datetime,
) -> None:
    """为 Range 场景准备一条导入链与 3 个媒体资产（对象由调用方上传）。"""
    from app.infrastructure.db.models.datasources import (
        DataSourceImportModel,
        DataSourceModel,
        MediaAssetModel,
    )

    data_source_id = uuid.uuid4()
    import_id = uuid.uuid4()
    session.execute(
        insert(DataSourceModel),
        [
            {
                "id": data_source_id,
                "project_id": result.project_id,
                "code": f"PERF-SRC-{run_id}",
                "name": "perf-baseline-source",
                "kind": "HUGGINGFACE",
                "repo_id": "perf-baseline/synthetic",
                "revision": "0" * 40,
                "license": "synthetic",
                "access_condition": "UNKNOWN",
                "is_gated": False,
                "created_at": now,
                "updated_at": now,
                "created_by": result.user_id,
                "updated_by": result.user_id,
            }
        ],
    )
    session.execute(
        insert(DataSourceImportModel),
        [
            {
                "id": import_id,
                "project_id": result.project_id,
                "data_source_id": data_source_id,
                "status": "SUCCEEDED",
                "scope": "SAMPLE",
                "sample_limit": 3,
                "tool_name": "perf-baseline",
                "tool_version": _PIPELINE_VERSION,
                "created_at": now,
                "updated_at": now,
                "created_by": result.user_id,
                "updated_by": result.user_id,
            }
        ],
    )
    media_rows: list[dict[str, Any]] = []
    for index in range(3):
        asset_id = uuid.uuid4()
        object_key = f"perf-baseline/{run_id}/video/{index:02d}.mp4"
        result.media_asset_ids.append(asset_id)
        result.object_keys.append(object_key)
        media_rows.append(
            {
                "id": asset_id,
                "project_id": result.project_id,
                "data_source_id": data_source_id,
                "import_id": import_id,
                "episode_key": f"episode-{index:02d}",
                "path": f"video/{index:02d}.mp4",
                "camera": f"camera-{index}",
                "object_key": object_key,
                "size_bytes": 4 * 1024 * 1024,
                "sha256": SHA256_ZERO,
                "content_type": "video/mp4",
                "task_name": "perf-baseline",
                "created_at": now,
                "updated_at": now,
                "created_by": result.user_id,
                "updated_by": result.user_id,
            }
        )
    session.execute(insert(MediaAssetModel), media_rows)
    session.flush()
    result.data_source_id = data_source_id
    result.import_id = import_id


def table_counts(dsn: str) -> dict[str, int]:
    """用 ORM 表定义取行数（不拼 SQL，表名不会成为注入面）。"""
    from app.infrastructure.db.models.datasets import DatasetModel
    from app.infrastructure.db.models.episodes import EpisodeModel
    from app.infrastructure.db.models.ingestion import RawAssetModel

    models = {
        "episodes": EpisodeModel,
        "raw_assets": RawAssetModel,
        "datasets": DatasetModel,
    }
    engine = create_engine(dsn)
    try:
        with Session(engine) as session:
            return {
                name: int(session.scalar(select(func.count()).select_from(model)) or 0)
                for name, model in models.items()
            }
    finally:
        engine.dispose()


class ObjectFixtures:
    """在对象存储写入/删除合成视频；复用现有 S3 适配器，不新写协议实现。"""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    @classmethod
    def from_settings(cls: type[ObjectFixtures]) -> ObjectFixtures:
        from app.core.config import get_settings
        from app.infrastructure.storage.s3 import S3MultipartStorage

        return cls(S3MultipartStorage(get_settings()))

    def upload_synthetic_videos(self, keys: Sequence[str], *, size_bytes: int) -> None:
        payload = synthetic_video_bytes(size_bytes)
        for key in keys:
            self._storage.put_bytes(key, payload, content_type="video/mp4")

    def delete(self, keys: Sequence[str]) -> list[str]:
        failures: list[str] = []
        for key in keys:
            try:
                self._storage.delete_object(key)
            except Exception as exc:  # 清理失败要如实上报，不能让报告"看起来干净"
                failures.append(f"{key}: {type(exc).__name__}: {exc}")
        return failures


def synthetic_video_bytes(size_bytes: int) -> bytes:
    """固定内容的伪 MP4：Range 基线只关心字节区间与对象大小，不解析编码。"""
    if size_bytes < 32:
        raise ValueError("合成视频至少 32 字节")
    header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    return header + b"\x00" * (size_bytes - len(header))
