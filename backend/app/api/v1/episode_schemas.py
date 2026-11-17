"""Episode HTTP DTO。

请求模型统一 ``extra="forbid"``；客户端不能提交 project_id、状态、MANIFEST
Hash、版本、谱系 ID、审计字段或时间戳等服务端权威字段。``manifest_key``
由服务端按 Episode 版本生成。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.episodes.models import Episode, Segmentation

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
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
