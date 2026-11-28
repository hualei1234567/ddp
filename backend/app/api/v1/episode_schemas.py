"""Episode HTTP DTO。

请求模型统一 ``extra="forbid"``；客户端不能提交 project_id、状态、MANIFEST
Hash、版本、谱系 ID、审计字段或时间戳等服务端权威字段。``manifest_key``
由服务端按 Episode 版本生成。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.episodes.enums import EpisodeStatus
from app.domain.episodes.models import Episode, Segmentation
from app.domain.episodes.state_machines import allowed_episode_actions
from app.infrastructure.repositories.video_frames import VideoFrameItem, VideoFramePage

_SEGMENTATION_METHOD_PATTERN = "^(MANUAL_TIME_RANGE|RULE)$"
_QUALITY_STATUS_PATTERN = "^(PASS|WARN|FAIL)$"


class SegmentationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(pattern=_SEGMENTATION_METHOD_PATTERN)
    rule_version: str = Field(min_length=1, max_length=64)
    start_time_ns: int | None = Field(default=None, ge=0)
    end_time_ns: int | None = Field(default=None, ge=0)
    time_domain: str = "UTC"
    allow_overlap: bool = False
    overlap_reason: str | None = Field(default=None, max_length=1000)
    rule_parameters: dict[str, object] = Field(default_factory=dict)

    def to_domain(self) -> Segmentation:
        return Segmentation(
            method=self.method,
            rule_version=self.rule_version,
            start_time_ns=self.start_time_ns,
            end_time_ns=self.end_time_ns,
            time_domain=self.time_domain,
            allow_overlap=self.allow_overlap,
            overlap_reason=self.overlap_reason,
            rule_parameters=self.rule_parameters,
        )


class EpisodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_asset_ids: list[UUID] = Field(min_length=1, max_length=64)
    segmentation: SegmentationIn
    time_base: dict[str, object]
    calibration_snapshot: list[dict[str, object]] = Field(default_factory=list)
    canonical_schema_version: str = Field(min_length=1, max_length=64)
    pipeline_id: str = Field(min_length=1, max_length=128)
    pipeline_version: str = Field(min_length=1, max_length=64)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    task_id: UUID | None = None
    batch_id: UUID | None = None
    robot_id: UUID | None = None
    capture_session_id: UUID | None = None
    device_ids: list[UUID] = Field(default_factory=list)
    sensor_ids: list[UUID] = Field(default_factory=list)
    scene_labels: list[str] = Field(default_factory=list)
    language_instruction: str | None = Field(default=None, max_length=1000)
    success: bool | None = None
    failure_reason: str | None = Field(default=None, max_length=1000)
    ingestion_job_ids: list[UUID] = Field(default_factory=list)
    evidence_role: str = Field(default="INPUT", min_length=1, max_length=32)


class EpisodeActionRequest(BaseModel):
    """状态动作载荷；``ready`` 需要 Manifest，其余动作禁止 Manifest 字段。"""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    quality_status: str | None = Field(default=None, pattern=_QUALITY_STATUS_PATTERN)
    manifest: dict[str, object] | None = None
    frame_count: int | None = Field(default=None, ge=0)


class EpisodeRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)


class ReadinessBlockerOut(BaseModel):
    """自动就绪被拒绝的一条原因。

    用精确模型而不是 ``dict[str, object]``：否则契约里声明的 ``code`` /
    ``message`` / ``detail`` 永远对不上实现形状，契约一致性检查只能把它们
    当成"契约多出来的字段"，真正的字段漂移反而被噪声掩盖。
    """

    code: str
    message: str
    detail: dict[str, object]


class EpisodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: UUID
    project_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    code: str
    task_id: UUID | None
    batch_id: UUID | None
    robot_id: UUID | None
    capture_session_id: UUID | None
    device_ids: list[UUID]
    sensor_ids: list[UUID]
    status: str
    quality_status: str
    annotation_status: str
    success: bool | None
    failure_reason: str | None
    scene_labels: list[str]
    language_instruction: str | None
    start_time_ns: int
    end_time_ns: int
    duration_ns: int
    time_base: dict[str, object]
    canonical_schema_version: str
    pipeline_id: str
    pipeline_version: str
    segmentation_rule_version: str
    frame_count: int
    calibration_snapshot: list[dict[str, object]]
    allowed_actions: list[str]
    manifest_key: str | None
    manifest_sha256: str | None
    # 自动就绪被拒绝的原因（[{"code","message","detail"}]）。空数组 + PENDING
    # 表示尚未尝试自动就绪；非空表示尝试过且明确被拒。这是「为什么它还没就绪」
    # 的权威来源，客户端不需要自己推断。
    readiness_blockers: list[ReadinessBlockerOut]
    preview_refs: list[dict[str, object]]
    supersedes_id: UUID | None
    attempt: int
    max_attempts: int
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, episode: Episode) -> EpisodeOut:
        return cls(
            episode_id=episode.episode_id,
            project_id=episode.project_id,
            episode_lineage_id=episode.episode_lineage_id,
            episode_version=episode.episode_version,
            code=episode.code,
            task_id=episode.task_id,
            batch_id=episode.batch_id,
            robot_id=episode.robot_id,
            capture_session_id=episode.capture_session_id,
            device_ids=list(episode.device_ids),
            sensor_ids=list(episode.sensor_ids),
            status=_value(episode.status),
            quality_status=_value(episode.quality_status),
            annotation_status=_value(episode.annotation_status),
            success=episode.success,
            failure_reason=episode.failure_reason,
            scene_labels=list(episode.scene_labels),
            language_instruction=episode.language_instruction,
            start_time_ns=episode.start_time_ns,
            end_time_ns=episode.end_time_ns,
            duration_ns=episode.duration_ns,
            time_base=dict(episode.time_base),
            canonical_schema_version=episode.canonical_schema_version,
            pipeline_id=episode.pipeline_id,
            pipeline_version=episode.pipeline_version,
            segmentation_rule_version=episode.segmentation_rule_version,
            frame_count=episode.frame_count,
            calibration_snapshot=[dict(item) for item in episode.calibration_snapshot],
            manifest_key=episode.manifest_key,
            manifest_sha256=episode.manifest_sha256,
            readiness_blockers=[
                ReadinessBlockerOut.model_validate(item.as_dict())
                for item in episode.readiness_blockers
            ],
            preview_refs=[dict(item) for item in episode.preview_refs],
            supersedes_id=episode.supersedes_id,
            attempt=episode.attempt,
            max_attempts=episode.max_attempts,
            version=episode.version,
            created_at=episode.created_at,
            updated_at=episode.updated_at,
            created_by=episode.created_by,
            updated_by=episode.updated_by,
            allowed_actions=sorted(
                _PUBLIC_ACTION_BY_DOMAIN[action]
                for action in allowed_episode_actions(EpisodeStatus(_value(episode.status)))
                if action in _PUBLIC_ACTION_BY_DOMAIN
            ),
        )


# 对外动作名 → 应用层具名动作。未暴露 system-*/retry/archive。
# 放在 DTO 层是因为「哪些动作对外可见」既决定响应里的 allowed_actions，
# 也决定路由接受的 action 取值——两处各写一份必然分叉。
EPISODE_ACTION_MAP: dict[str, str] = {
    "ready": "ready",
    "quality-check": "quality-check",
    "quality-result": "record-quality",
    "accept": "accept",
    "reject": "reject",
    "complete": "complete",
    # 归档是数据生命周期的正常终点（COMPLETED → ARCHIVED）：长训数据集里
    # 已经用过的 Episode 不必永久占据"活跃"列表，但历史与血缘必须保留。
    "archive": "archive",
    "invalidate": "invalidate",
}
_PUBLIC_ACTION_BY_DOMAIN = {internal: public for public, internal in EPISODE_ACTION_MAP.items()}


class VideoFrameOut(BaseModel):
    """单帧视频索引只读响应。"""

    model_config = ConfigDict(extra="forbid")

    frame_index: int
    stream_id: str
    pts_seconds: float | None
    timestamp_raw: str | None
    timestamp_normalized: float | None
    normalize_method: str | None
    time_base: str | None
    is_keyframe: bool
    decode_status: str
    error_message: str | None

    @classmethod
    def from_record(cls, record: VideoFrameItem) -> VideoFrameOut:
        return cls(
            frame_index=record.frame_index,
            stream_id=record.stream_id,
            pts_seconds=(float(record.pts_seconds) if record.pts_seconds is not None else None),
            timestamp_raw=record.timestamp_raw,
            timestamp_normalized=(
                float(record.timestamp_normalized_seconds)
                if record.timestamp_normalized_seconds is not None
                else None
            ),
            normalize_method=record.normalize_method,
            time_base=record.time_base,
            is_keyframe=record.is_keyframe,
            decode_status=record.decode_status,
            error_message=record.error_message,
        )


class EpisodeVideoFramesOut(BaseModel):
    """Episode 视频帧索引分页与汇总响应。"""

    model_config = ConfigDict(extra="forbid")

    episode_id: UUID
    raw_asset_ids: list[UUID]
    total_frames: int
    decoded_count: int
    failed_count: int
    normalized_count: int
    limit: int
    offset: int
    items: list[VideoFrameOut]

    @classmethod
    def from_page(cls, page: VideoFramePage) -> EpisodeVideoFramesOut:
        return cls(
            episode_id=page.episode_id,
            raw_asset_ids=list(page.raw_asset_ids),
            total_frames=page.summary.total_frames,
            decoded_count=page.summary.decoded_count,
            failed_count=page.summary.failed_count,
            normalized_count=page.summary.normalized_count,
            limit=page.limit,
            offset=page.offset,
            items=[VideoFrameOut.from_record(item) for item in page.items],
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
