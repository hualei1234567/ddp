"""自动就绪判定的输入事实（纯数据，无规则、无 IO）。

把"事实"与"规则"分开：本模块只描述**从库里读到的真实事实**，判定逻辑在
``app.domain.episodes.readiness``。这样领域单测可以直接构造事实，不需要造
Episode 实体、更不需要数据库。

事实来源（唯一来源，不另造一份）：

- ``EpisodeStream``：``EpisodeRawAsset``（关联时的 Hash/大小快照）+
  ``RawAsset``（object_key/sha256/size_bytes/content_type/format/status）+
  ``RawAsset.raw_metadata`` 里的来源事实（``camera`` / ``source_fps``，
  导入链路由 ``import_runner._import_metadata`` 写入）；
- ``EpisodeManifestFacts``：``Episode`` 实体的身份与时间字段，
  ``from_episode`` 是唯一转换入口。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.domain.episodes.models import Episode


@dataclass(frozen=True, slots=True)
class EpisodeStream:
    """一条机位流在判定时刻的事实快照（来自 RawAsset 与 EpisodeRawAsset）。

    生成器只读这些字段，不直接碰 ORM/仓储：同一份事实必然得到同一份 Manifest。
    ``usable=False`` 表示关联指向的资产不可取（已被删除/不可见）——这是阻塞项，
    但不是异常：事实缺失就如实登记，不猜测。
    """

    asset_id: UUID
    sha256: str
    size_bytes: int
    camera: str | None = None
    object_key: str | None = None
    content_type: str | None = None
    format: str | None = None
    source_fps: float | None = None
    # EpisodeRawAsset 在创建时对 Hash/大小做的快照；与当前资产不一致说明
    # 关联已与资产脱节（资产不可变，脱节只可能来自数据被绕过写入或历史行）。
    sha256_snapshot: str | None = None
    size_bytes_snapshot: int | None = None
    usable: bool = True
    status: str | None = None


@dataclass(frozen=True, slots=True)
class EpisodeManifestFacts:
    """生成 Manifest 所需的 Episode 侧事实（与 Episode 实体解耦，便于单测）。

    与 ``Episode`` 的对应关系是 1:1，且全部取自已落库字段；
    ``from_episode`` 是唯一的转换入口。
    """

    episode_id: UUID
    project_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    code: str
    start_time_ns: int
    end_time_ns: int
    time_base: Mapping[str, object]
    calibration_snapshot: Sequence[Mapping[str, object]]
    canonical_schema_version: str
    pipeline_id: str
    pipeline_version: str
    quality_status: str
    task_id: UUID | None = None
    batch_id: UUID | None = None
    robot_id: UUID | None = None
    device_ids: tuple[UUID, ...] = ()
    sensor_ids: tuple[UUID, ...] = ()

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns

    @classmethod
    def from_episode(cls, episode: Episode) -> EpisodeManifestFacts:
        return cls(
            episode_id=episode.episode_id,
            project_id=episode.project_id,
            episode_lineage_id=episode.episode_lineage_id,
            episode_version=episode.episode_version,
            code=episode.code,
            start_time_ns=episode.start_time_ns,
            end_time_ns=episode.end_time_ns,
            time_base=dict(episode.time_base),
            calibration_snapshot=tuple(dict(item) for item in episode.calibration_snapshot),
            canonical_schema_version=episode.canonical_schema_version,
            pipeline_id=episode.pipeline_id,
            pipeline_version=episode.pipeline_version,
            quality_status=str(getattr(episode.quality_status, "value", episode.quality_status)),
            task_id=episode.task_id,
            batch_id=episode.batch_id,
            robot_id=episode.robot_id,
            device_ids=tuple(episode.device_ids),
            sensor_ids=tuple(episode.sensor_ids),
        )
