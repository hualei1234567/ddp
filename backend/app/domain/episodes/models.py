"""Episode、切片规则与 Episode-RawAsset 关联聚合。

READY 起冻结数据字段；ACCEPTED 起冻结质量结论。修订必须创建新的
``episode_id`` 与 ``episode_version``，旧版本保持原状态不变。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from app.domain.episodes.enums import (
    EpisodeAnnotationStatus,
    EpisodeQualityStatus,
    EpisodeStatus,
    SegmentationMethod,
)
from app.domain.episodes.errors import (
    DomainValidationError,
    ResourceImmutableError,
)
from app.domain.episodes.manifest import (
    assert_manifest_hash,
    compute_manifest_sha256,
    validate_manifest_identity,
)
from app.domain.episodes.readiness_result import (
    ReadinessBlocker,
    normalize_readiness_blockers,
)
from app.domain.episodes.state_machines import (
    EPISODE_DATA_FROZEN_FIELDS,
    EPISODE_DATA_FROZEN_STATUSES,
    episode_target_status,
)
from app.domain.episodes.validation import (
    CODE_MAX_LENGTH,
    REASON_MAX_LENGTH,
    VERSION_TEXT_MAX_LENGTH,
    VERSION_TEXT_SHORT_MAX_LENGTH,
    build_manifest_key,
    non_negative_int,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_text,
    time_range,
    uuid_tuple,
    validate_calibration_snapshot,
    validate_manifest_key,
    validate_scene_labels,
    validate_sha256,
    validate_time_base,
)


@dataclass(frozen=True, slots=True)
class Segmentation:
    """切片规则；阶段 2 只允许人工时间范围与已注册规则。"""

    method: SegmentationMethod | str
    rule_version: str
    start_time_ns: int | None = None
    end_time_ns: int | None = None
    time_domain: str = "UTC"
    allow_overlap: bool = False
    overlap_reason: str | None = None
    rule_parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            method = SegmentationMethod(self.method)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("segmentation.method 取值非法") from exc
        if method is SegmentationMethod.PARSER_SEGMENT:
            raise DomainValidationError("阶段 2 不支持解析器切片")
        object.__setattr__(self, "method", method)
        object.__setattr__(
            self,
            "rule_version",
            required_text(
                self.rule_version,
                "segmentation.rule_version",
                max_length=VERSION_TEXT_SHORT_MAX_LENGTH,
            ),
        )
        if self.time_domain != "UTC":
            raise DomainValidationError("segmentation.time_domain 必须为 UTC")
        if method is SegmentationMethod.MANUAL_TIME_RANGE:
            if self.start_time_ns is None or self.end_time_ns is None:
                raise DomainValidationError("MANUAL_TIME_RANGE 必须提供起止时间")
        elif method is SegmentationMethod.RULE:
            if not self.rule_parameters:
                raise DomainValidationError("RULE 切片必须提供规则参数")
        start, end = time_range(self.start_time_ns, self.end_time_ns)
        object.__setattr__(self, "start_time_ns", start)
        object.__setattr__(self, "end_time_ns", end)
        if not isinstance(self.allow_overlap, bool):
            raise DomainValidationError("segmentation.allow_overlap 必须是布尔值")
        object.__setattr__(
            self,
            "overlap_reason",
            optional_text(
                self.overlap_reason,
                "segmentation.overlap_reason",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        if self.allow_overlap and self.overlap_reason is None:
            raise DomainValidationError("允许重叠时必须提供 overlap_reason")
        if not self.allow_overlap and self.overlap_reason is not None:
            raise DomainValidationError("未允许重叠时不得提供 overlap_reason")
        if not isinstance(self.rule_parameters, Mapping):
            raise DomainValidationError("segmentation.rule_parameters 必须是对象")
        object.__setattr__(self, "rule_parameters", dict(self.rule_parameters))

    @property
    def range(self) -> tuple[int, int]:
        assert self.start_time_ns is not None and self.end_time_ns is not None
        return self.start_time_ns, self.end_time_ns


@dataclass(frozen=True, slots=True)
class EpisodeRawAsset:
    """Episode 与 RawAsset 的切片关系；创建后只追加。"""

    project_id: UUID
    episode_id: UUID
    asset_id: UUID
    role: str
    sha256_snapshot: str
    size_bytes_snapshot: int
    start_time_ns: int | None = None
    end_time_ns: int | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "episode_id", "asset_id"):
            if not isinstance(getattr(self, name), UUID):
                raise DomainValidationError(f"{name} 必须是 UUID")
        object.__setattr__(
            self,
            "role",
            required_text(self.role, "role", max_length=32),
        )
        object.__setattr__(
            self,
            "sha256_snapshot",
            validate_sha256(self.sha256_snapshot, "sha256_snapshot"),
        )
        object.__setattr__(
            self,
            "size_bytes_snapshot",
            positive_int(self.size_bytes_snapshot, "size_bytes_snapshot"),
        )
        if (self.start_time_ns is None) != (self.end_time_ns is None):
            raise DomainValidationError("start_time_ns 与 end_time_ns 必须同时提供或同时为空")
        if self.start_time_ns is not None and self.end_time_ns is not None:
            start, end = time_range(self.start_time_ns, self.end_time_ns)
            object.__setattr__(self, "start_time_ns", start)
            object.__setattr__(self, "end_time_ns", end)


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: UUID
    project_id: UUID
    code: str
    start_time_ns: int
    end_time_ns: int
    time_base: Mapping[str, object]
    canonical_schema_version: str
    pipeline_id: str
    pipeline_version: str
    segmentation_rule_version: str
    calibration_snapshot: Sequence[Mapping[str, object]]
    episode_lineage_id: UUID | None = None
    episode_version: int = 1
    task_id: UUID | None = None
    batch_id: UUID | None = None
    robot_id: UUID | None = None
    capture_session_id: UUID | None = None
    device_ids: tuple[UUID, ...] = ()
    sensor_ids: tuple[UUID, ...] = ()
    status: EpisodeStatus | str = EpisodeStatus.PENDING
    quality_status: EpisodeQualityStatus | str = EpisodeQualityStatus.NOT_CHECKED
    annotation_status: EpisodeAnnotationStatus | str = EpisodeAnnotationStatus.NOT_STARTED
    success: bool | None = None
    failure_reason: str | None = None
    scene_labels: tuple[str, ...] = ()
    language_instruction: str | None = None
    frame_count: int = 0
    manifest_key: str | None = None
    manifest_sha256: str | None = None
    # 最近一次自动就绪判定的拒绝原因（可查询、面向人）。
    # 空元组 + PENDING = 尚未尝试自动就绪；非空 = 尝试过且被明确拒绝。
    # 这是运维事实字段，不属于 ``EPISODE_DATA_FROZEN_FIELDS``（数据字段）。
    readiness_blockers: tuple[ReadinessBlocker, ...] = ()
    preview_refs: tuple[Mapping[str, object], ...] = ()
    supersedes_id: UUID | None = None
    attempt: int = 1
    max_attempts: int = 3
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("episode_id", "project_id"):
            if not isinstance(getattr(self, name), UUID):
                raise DomainValidationError(f"{name} 必须是 UUID")
        object.__setattr__(
            self,
            "code",
            required_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        start, end = time_range(self.start_time_ns, self.end_time_ns)
        object.__setattr__(self, "start_time_ns", start)
        object.__setattr__(self, "end_time_ns", end)
        object.__setattr__(self, "time_base", validate_time_base(self.time_base))
        object.__setattr__(
            self,
            "canonical_schema_version",
            required_text(
                self.canonical_schema_version,
                "canonical_schema_version",
                max_length=VERSION_TEXT_SHORT_MAX_LENGTH,
            ),
        )
        for name, limit in (
            ("pipeline_id", VERSION_TEXT_MAX_LENGTH),
            ("pipeline_version", VERSION_TEXT_SHORT_MAX_LENGTH),
            ("segmentation_rule_version", VERSION_TEXT_SHORT_MAX_LENGTH),
        ):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), name, max_length=limit),
            )
        object.__setattr__(
            self,
            "calibration_snapshot",
            validate_calibration_snapshot(self.calibration_snapshot),
        )
        lineage_id = self.episode_lineage_id or self.episode_id
        if not isinstance(lineage_id, UUID):
            raise DomainValidationError("episode_lineage_id 必须是 UUID")
        object.__setattr__(self, "episode_lineage_id", lineage_id)
        object.__setattr__(
            self,
            "episode_version",
            positive_int(self.episode_version, "episode_version"),
        )
        for name in (
            "task_id",
            "batch_id",
            "robot_id",
            "capture_session_id",
            "supersedes_id",
            "created_by",
            "updated_by",
        ):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(self, "device_ids", uuid_tuple(self.device_ids, "device_ids"))
        object.__setattr__(self, "sensor_ids", uuid_tuple(self.sensor_ids, "sensor_ids"))
        object.__setattr__(self, "status", _enum(self.status, EpisodeStatus, "status"))
        object.__setattr__(
            self,
            "quality_status",
            _enum(self.quality_status, EpisodeQualityStatus, "quality_status"),
        )
        object.__setattr__(
            self,
            "annotation_status",
            _enum(self.annotation_status, EpisodeAnnotationStatus, "annotation_status"),
        )
        if self.success is not None and not isinstance(self.success, bool):
            raise DomainValidationError("success 必须是布尔值或 null")
        object.__setattr__(
            self,
            "failure_reason",
            optional_text(self.failure_reason, "failure_reason", max_length=REASON_MAX_LENGTH),
        )
        if self.success is False and self.failure_reason is None:
            raise DomainValidationError("采集失败必须记录 failure_reason")
        object.__setattr__(self, "scene_labels", validate_scene_labels(self.scene_labels))
        object.__setattr__(
            self,
            "language_instruction",
            optional_text(
                self.language_instruction,
                "language_instruction",
                max_length=REASON_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "frame_count", non_negative_int(self.frame_count, "frame_count"))
        object.__setattr__(
            self,
            "manifest_key",
            None if self.manifest_key is None else validate_manifest_key(self.manifest_key),
        )
        object.__setattr__(
            self,
            "manifest_sha256",
            None
            if self.manifest_sha256 is None
            else validate_sha256(self.manifest_sha256, "manifest_sha256"),
        )
        object.__setattr__(
            self,
            "readiness_blockers",
            normalize_readiness_blockers(self.readiness_blockers),
        )
        if self.readiness_blockers and self.status in EPISODE_DATA_FROZEN_STATUSES:
            # 数据已冻结（READY 起）却还挂着"为什么没就绪"的原因，说明状态与
            # 事实互相矛盾；宁可拒绝写入，也不留一条自相矛盾的行。
            raise DomainValidationError("READY 之后不得残留 readiness_blockers")
        preview_refs: list[dict[str, object]] = []
        for item in self.preview_refs:
            if not isinstance(item, Mapping):
                raise DomainValidationError("preview_refs 只能包含对象")
            preview_refs.append(dict(item))
        object.__setattr__(self, "preview_refs", tuple(preview_refs))
        object.__setattr__(self, "attempt", positive_int(self.attempt, "attempt"))
        object.__setattr__(self, "max_attempts", positive_int(self.max_attempts, "max_attempts"))
        if self.attempt > self.max_attempts:
            raise DomainValidationError("attempt 不得超过 max_attempts")
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        if self.status in EPISODE_DATA_FROZEN_STATUSES:
            if self.manifest_key is None or self.manifest_sha256 is None:
                raise DomainValidationError("READY 起必须记录 manifest_key 与 manifest_sha256")
        if self.status is EpisodeStatus.REJECTED and self.failure_reason is None:
            raise DomainValidationError("REJECTED Episode 必须记录 failure_reason")

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns

    @property
    def is_data_frozen(self) -> bool:
        return self.status in EPISODE_DATA_FROZEN_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            EpisodeStatus.REJECTED,
            EpisodeStatus.ARCHIVED,
            EpisodeStatus.INVALIDATED,
        }

    def data_snapshot(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in EPISODE_DATA_FROZEN_FIELDS}

    def assert_data_immutable(self, updated: Episode) -> None:
        current = self.data_snapshot()
        candidate = updated.data_snapshot()
        changed = sorted(name for name in current if current[name] != candidate[name])
        if changed:
            raise ResourceImmutableError("Episode READY 后数据字段不可变")

    def start_processing(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "system-start")
        return replace(self, status=target, updated_at=now)

    def mark_ready(
        self,
        *,
        manifest_key: str,
        manifest: Mapping[str, object],
        frame_count: int,
        now: datetime,
    ) -> Episode:
        target = episode_target_status(self.status, "system-ready")
        expected_key = build_manifest_key(self.project_id, self.episode_id, self.episode_version)
        if manifest_key != expected_key:
            raise DomainValidationError("manifest_key 必须由服务端按 Episode 版本生成")
        validate_manifest_identity(
            manifest,
            episode_id=self.episode_id,
            episode_lineage_id=self.episode_lineage_id,
            episode_version=self.episode_version,
            project_id=self.project_id,
            code=self.code,
            start_time_ns=self.start_time_ns,
            end_time_ns=self.end_time_ns,
            duration_ns=self.duration_ns,
        )
        manifest_sha256 = compute_manifest_sha256(manifest)
        return replace(
            self,
            status=target,
            manifest_key=manifest_key,
            manifest_sha256=manifest_sha256,
            frame_count=non_negative_int(frame_count, "frame_count"),
            # 就绪即意味着此前的阻塞原因已消解；残留会让"为什么没就绪"永远有答案。
            readiness_blockers=(),
            updated_at=now,
        )

    def record_readiness_blocked(
        self,
        *,
        blockers: tuple[ReadinessBlocker, ...],
        now: datetime,
    ) -> Episode:
        """登记"为什么还不能自动就绪"；**状态保持 PENDING 不变**。

        只有 PENDING 可以登记：其余状态要么已就绪（原因已消解），要么已被人工
        驳回/失效（原因由 ``failure_reason`` 承载），此时再写阻塞项会让两个
        来源互相覆盖。
        """
        normalized = normalize_readiness_blockers(blockers)
        if not normalized:
            raise DomainValidationError("登记阻塞原因时至少要有一条")
        if self.status is not EpisodeStatus.PENDING:
            raise DomainValidationError(
                f"只有 PENDING 的 Episode 可以登记就绪阻塞原因，当前为 {self.status.value}"
            )
        return replace(self, readiness_blockers=normalized, updated_at=now)

    def assert_manifest_unchanged(self, manifest: Mapping[str, object]) -> None:
        if self.manifest_sha256 is None:
            raise DomainValidationError("Manifest 尚未生成")
        assert_manifest_hash(manifest, self.manifest_sha256)

    def mark_failed(
        self,
        *,
        error_code: str,
        error_summary: str,
        now: datetime,
    ) -> Episode:
        target = episode_target_status(self.status, "system-fail")
        code = required_text(error_code, "error_code", max_length=64)
        summary = required_text(error_summary, "error_summary", max_length=REASON_MAX_LENGTH)
        return replace(
            self,
            status=target,
            failure_reason=f"{code}: {summary}",
            updated_at=now,
        )

    def retry_failed(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "retry")
        if self.attempt >= self.max_attempts:
            raise DomainValidationError("Episode 已达到最大重试次数")
        return replace(
            self,
            status=target,
            attempt=self.attempt + 1,
            failure_reason=None,
            updated_at=now,
        )

    def begin_quality_check(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "quality-check")
        return replace(
            self,
            status=target,
            quality_status=EpisodeQualityStatus.NOT_CHECKED,
            updated_at=now,
        )

    def record_quality_result(
        self,
        result: EpisodeQualityStatus | str,
        now: datetime,
    ) -> Episode:
        if self.status is not EpisodeStatus.QUALITY_CHECKING:
            raise DomainValidationError("只有 QUALITY_CHECKING 状态可以写入质检结论")
        quality = _enum(result, EpisodeQualityStatus, "quality_status")
        if quality is EpisodeQualityStatus.NOT_CHECKED:
            raise DomainValidationError("质检结论不能是 NOT_CHECKED")
        return replace(self, quality_status=quality, updated_at=now)

    def accept(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "accept")
        if self.quality_status is not EpisodeQualityStatus.PASS:
            raise DomainValidationError("质检未通过，不能 ACCEPTED")
        return replace(self, status=target, updated_at=now)

    def reject(self, *, reason: str, now: datetime) -> Episode:
        target = episode_target_status(self.status, "reject")
        detail = required_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        return replace(
            self,
            status=target,
            quality_status=EpisodeQualityStatus.FAIL,
            failure_reason=detail,
            updated_at=now,
        )

    def complete(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "complete")
        if self.quality_status is not EpisodeQualityStatus.PASS:
            raise DomainValidationError("质检未通过，不能 COMPLETED")
        return replace(self, status=target, updated_at=now)

    def archive(self, now: datetime) -> Episode:
        target = episode_target_status(self.status, "archive")
        return replace(self, status=target, updated_at=now)

    def invalidate(self, *, reason: str, now: datetime) -> Episode:
        target = episode_target_status(self.status, "invalidate")
        detail = required_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        return replace(self, status=target, failure_reason=detail, updated_at=now)

    def next_revision(
        self,
        *,
        episode_id: UUID,
        code: str,
        now: datetime,
    ) -> Episode:
        """生成新版本；不修改当前对象。修订必须显式提供新 code。"""
        if not isinstance(episode_id, UUID):
            raise DomainValidationError("episode_id 必须是 UUID")
        if episode_id == self.episode_id:
            raise DomainValidationError("修订必须使用新的 episode_id")
        if not self.is_data_frozen and self.status is not EpisodeStatus.REJECTED:
            raise DomainValidationError("只有 READY 之后或已驳回的 Episode 才能修订")
        return replace(
            self,
            episode_id=episode_id,
            code=required_text(code, "code", max_length=CODE_MAX_LENGTH),
            episode_lineage_id=self.episode_lineage_id,
            episode_version=self.episode_version + 1,
            status=EpisodeStatus.PENDING,
            quality_status=EpisodeQualityStatus.NOT_CHECKED,
            annotation_status=EpisodeAnnotationStatus.NOT_STARTED,
            manifest_key=None,
            manifest_sha256=None,
            frame_count=0,
            readiness_blockers=(),
            supersedes_id=self.episode_id,
            attempt=1,
            version=1,
            created_at=now,
            updated_at=now,
            updated_by=self.updated_by,
        )


def _enum(value: object, enum_type: type, field_name: str) -> object:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc
