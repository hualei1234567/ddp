"""标注提交与标注记录聚合。

提交版本化：草稿可编辑；一旦离开 DRAFT，内容列由实体与数据库触发器共同冻结。
``records_digest`` 与 ``record_sha256`` 均由稳定 JSON 计算，任何内容变化都会得到
不同 Hash，用于提交完整性与防篡改校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationLabelType,
    AnnotationRecordStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
    DataSource,
    LabelValueSource,
    LabelValueStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    LabelValueInvalidError,
    RecordDigestMismatchError,
    ResourceImmutableError,
    SubmissionIncompleteError,
    UnknownLabelError,
)
from app.domain.annotation.schema import LabelDefinition
from app.domain.annotation.state_machines import (
    action_requires_reason,
    record_target,
    submission_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    JSON_MAX_BYTES,
    REASON_MAX_LENGTH,
    canonical_json,
    canonical_sha256,
    check_ns_range,
    coerce_enum,
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    validate_sha256,
)

_JSON_VALUE_TYPES = (str, int, float, bool, list, tuple, dict)
_TIME_RANGE_KEYS = frozenset({"start_time_ns", "end_time_ns", "time_domain"})
_TIME_DOMAIN_VALUES = frozenset({"UTC", "ROS", "DEVICE", "HOST", "MONOTONIC", "UNKNOWN"})

# 提交可见性：草稿只在作者本人之外不可见，其余状态对项目内可见。
# 规则只在这里定义一次；API 过滤、仓储下推查询都引用本函数/本常量，禁止各自复制。
VISIBLE_DRAFT_STATUS = AnnotationSubmissionStatus.DRAFT.value


def submission_visible_to(
    status: object,
    annotator_id: UUID,
    actor_id: UUID,
) -> bool:
    """提交对 ``actor_id`` 是否可见：``DRAFT`` 仅本人可见。"""
    if getattr(status, "value", status) != VISIBLE_DRAFT_STATUS:
        return True
    return annotator_id == actor_id


# 已作出终态结论的提交：复核/仲裁都不能再次决定。
# 与 ``state_transition_invalid`` 的区别：非法迁移是"这个动作在这一状态根本
# 不存在"；本码是"动作合法但已有人做过、且结论不可覆盖"，客户端应展示结论
# 而不是重试。契约把两者并存，因此不能只靠非法迁移代替（见 error-codes.v1.json）。
DECIDED_SUBMISSION_STATUSES = frozenset(
    {
        AnnotationSubmissionStatus.APPROVED.value,
        AnnotationSubmissionStatus.REJECTED.value,
        AnnotationSubmissionStatus.RESOLVED_APPROVED.value,
        AnnotationSubmissionStatus.RESOLVED_REJECTED.value,
        AnnotationSubmissionStatus.RESOLVED_REWORK.value,
        AnnotationSubmissionStatus.REWORK_REQUIRED.value,
        AnnotationSubmissionStatus.SUPERSEDED.value,
    }
)


def submission_already_decided(status: object) -> bool:
    """提交是否已有终态结论（复核/仲裁不得重复决定）。"""
    return _status_value(status) in DECIDED_SUBMISSION_STATUSES


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _validated_json(value: object) -> object:
    if not isinstance(value, _JSON_VALUE_TYPES) or value is None:
        raise LabelValueInvalidError("标签值必须是合法 JSON 值")
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise LabelValueInvalidError("标签值序列化后过大")
    return _plain(value)


@dataclass(frozen=True, slots=True)
class LabelValue:
    label_key: str
    value: object
    source: LabelValueSource | str = LabelValueSource.HUMAN
    label_status: LabelValueStatus | str = LabelValueStatus.CONFIRMED
    confidence: float | None = None
    comment: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "label_key",
            normalized_text(self.label_key, "label_key", max_length=100),
        )
        object.__setattr__(self, "value", _validated_json(self.value))
        object.__setattr__(
            self,
            "source",
            coerce_enum(self.source, LabelValueSource, "source"),
        )
        object.__setattr__(
            self,
            "label_status",
            coerce_enum(self.label_status, LabelValueStatus, "label_status"),
        )
        if self.confidence is not None:
            if (
                not isinstance(self.confidence, int | float)
                or isinstance(self.confidence, bool)
                or not 0 <= float(self.confidence) <= 1
            ):
                raise LabelValueInvalidError("confidence 必须在 0 到 1 之间")
        object.__setattr__(
            self,
            "comment",
            optional_text(self.comment, "comment", max_length=2000),
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "label_key": self.label_key,
            "value": _plain(self.value),
            "source": self.source.value,
            "label_status": self.label_status.value,
        }
        if self.confidence is not None:
            payload["confidence"] = float(self.confidence)
        if self.comment is not None:
            payload["comment"] = self.comment
        return payload


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    object_key: str
    sha256: str
    content_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            normalized_text(self.evidence_id, "evidence_id", max_length=64),
        )
        object.__setattr__(
            self,
            "object_key",
            normalized_text(self.object_key, "object_key", max_length=1024),
        )
        object.__setattr__(self, "sha256", validate_sha256(self.sha256, "sha256"))
        object.__setattr__(
            self,
            "content_type",
            optional_text(self.content_type, "content_type", max_length=200),
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "evidence_id": self.evidence_id,
            "object_key": self.object_key,
            "sha256": self.sha256,
        }
        if self.content_type is not None:
            payload["content_type"] = self.content_type
        return payload


def _normalize_time_range(value: object, field_name: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    spec = frozen_mapping(value, field_name)
    unknown = set(spec) - _TIME_RANGE_KEYS
    if unknown:
        raise DomainValidationError(f"{field_name} 包含未知字段")
    if "start_time_ns" not in spec or "end_time_ns" not in spec:
        raise DomainValidationError(f"{field_name} 必须包含起止时间")
    check_ns_range(spec["start_time_ns"], spec["end_time_ns"], field_name)
    domain = spec.get("time_domain", "UTC")
    if domain not in _TIME_DOMAIN_VALUES:
        raise DomainValidationError(f"{field_name}.time_domain 取值非法")
    return frozen_mapping({**spec, "time_domain": domain}, field_name)


def _normalize_labels(value: object) -> tuple[LabelValue, ...]:
    if not isinstance(value, list | tuple) or not value:
        raise SubmissionIncompleteError("标注记录至少需要一个标签值")
    labels: list[LabelValue] = []
    seen: set[str] = set()
    for item in value:
        label = item if isinstance(item, LabelValue) else LabelValue(**item)  # type: ignore[arg-type]
        if label.label_key in seen:
            raise LabelValueInvalidError("同一记录内标签键不得重复")
        seen.add(label.label_key)
        labels.append(label)
    return tuple(labels)


def _normalize_evidence(value: object) -> tuple[EvidenceRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("evidence_refs 必须是列表")
    refs: list[EvidenceRef] = []
    seen: set[str] = set()
    for item in value:
        ref = item if isinstance(item, EvidenceRef) else EvidenceRef(**item)  # type: ignore[arg-type]
        if ref.evidence_id in seen:
            raise DomainValidationError("evidence_id 不得重复")
        seen.add(ref.evidence_id)
        refs.append(ref)
    return tuple(refs)


def record_payload(record: AnnotationRecord) -> dict[str, object]:
    """记录 Hash 的稳定载荷；标签按 label_key 排序。"""
    return {
        "annotation_lineage_id": str(record.annotation_lineage_id),
        "annotation_version": record.annotation_version,
        "project_id": str(record.project_id),
        "submission_id": str(record.submission_id),
        "episode_id": str(record.episode_id),
        "episode_version": record.episode_version,
        "task_id": str(record.task_id),
        "schema_id": str(record.schema_id),
        "schema_version": record.schema_version,
        "time_range": _plain(record.time_range),
        "labels": [
            item.to_payload() for item in sorted(record.labels, key=lambda label: label.label_key)
        ],
        "evidence_refs": [
            item.to_payload()
            for item in sorted(record.evidence_refs, key=lambda ref: ref.evidence_id)
        ],
    }


def compute_record_sha256(record: AnnotationRecord) -> str:
    return canonical_sha256(record_payload(record))


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    annotation_id: UUID
    annotation_lineage_id: UUID
    annotation_version: int
    project_id: UUID
    submission_id: UUID
    episode_id: UUID
    episode_version: int
    task_id: UUID
    schema_id: UUID
    schema_version: int
    labels: tuple[LabelValue, ...]
    status: AnnotationRecordStatus | str = AnnotationRecordStatus.DRAFT
    time_range: Mapping[str, object] | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    record_sha256: str | None = None
    supersedes_id: UUID | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "annotation_id",
            "annotation_lineage_id",
            "project_id",
            "submission_id",
            "episode_id",
            "task_id",
            "schema_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "annotation_version",
            positive_int(self.annotation_version, "annotation_version"),
        )
        object.__setattr__(
            self,
            "episode_version",
            positive_int(self.episode_version, "episode_version"),
        )
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "schema_version"),
        )
        object.__setattr__(self, "labels", _normalize_labels(self.labels))
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationRecordStatus, "status"),
        )
        object.__setattr__(
            self,
            "time_range",
            _normalize_time_range(self.time_range, "time_range"),
        )
        object.__setattr__(self, "evidence_refs", _normalize_evidence(self.evidence_refs))
        object.__setattr__(
            self,
            "supersedes_id",
            optional_uuid(self.supersedes_id, "supersedes_id"),
        )
        object.__setattr__(self, "approved_by", optional_uuid(self.approved_by, "approved_by"))
        object.__setattr__(
            self,
            "approved_at",
            optional_aware_datetime(self.approved_at, "approved_at"),
        )
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        digest = compute_record_sha256(self)
        if self.record_sha256 is not None:
            provided = validate_sha256(self.record_sha256, "record_sha256")
            if provided != digest:
                raise RecordDigestMismatchError("record_sha256 与记录内容不一致")
        object.__setattr__(self, "record_sha256", digest)

    def apply_action(
        self,
        action: object,
        *,
        actor_id: UUID | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationRecord:
        action_name = validate_action_name(action, family="record")
        target = record_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("record", action_name) and cleaned_reason is None:
            raise DomainValidationError("该记录动作必须填写原因")
        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if target is AnnotationRecordStatus.APPROVED:
            changes["approved_by"] = optional_uuid(actor_id, "approved_by")
            changes["approved_at"] = occurred_at
        return replace(self, **changes)

    def assert_content_immutable(self, candidate: AnnotationRecord) -> None:
        if self.status is not AnnotationRecordStatus.DRAFT and record_payload(
            self
        ) != record_payload(candidate):
            raise ResourceImmutableError("已提交标注记录不可原地修改")


def records_payload(records: tuple[AnnotationRecord, ...]) -> list[dict[str, object]]:
    ordered = sorted(
        records,
        key=lambda item: (
            str(item.episode_id),
            str(item.annotation_lineage_id),
            item.annotation_version,
        ),
    )
    return [
        {
            "annotation_id": str(item.annotation_id),
            "annotation_lineage_id": str(item.annotation_lineage_id),
            "annotation_version": item.annotation_version,
            "episode_id": str(item.episode_id),
            "episode_version": item.episode_version,
            "time_range": _plain(item.time_range),
            "labels": [
                label.to_payload()
                for label in sorted(item.labels, key=lambda label: label.label_key)
            ],
            "evidence_refs": [
                ref.to_payload()
                for ref in sorted(item.evidence_refs, key=lambda ref: ref.evidence_id)
            ],
            "record_sha256": item.record_sha256,
        }
        for item in ordered
    ]


def compute_records_digest(records: tuple[AnnotationRecord, ...]) -> str:
    return canonical_sha256({"records": records_payload(records)})


@dataclass(frozen=True, slots=True)
class AnnotationSubmission:
    submission_id: UUID
    project_id: UUID
    task_id: UUID
    batch_id: UUID
    assignment_id: UUID
    annotator_id: UUID
    submission_version: int
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    records_digest: str
    item_count: int
    status: AnnotationSubmissionStatus | str = AnnotationSubmissionStatus.DRAFT
    data_source: DataSource | str = DataSource.UNKNOWN
    record_refs: tuple[UUID, ...] = ()
    records: tuple[AnnotationRecord, ...] = ()
    review_id: UUID | None = None
    arbitration_id: UUID | None = None
    supersedes_id: UUID | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "submission_id",
            "project_id",
            "task_id",
            "batch_id",
            "assignment_id",
            "annotator_id",
            "schema_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "submission_version",
            positive_int(self.submission_version, "submission_version"),
        )
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "schema_version"),
        )
        object.__setattr__(
            self,
            "schema_sha256",
            validate_sha256(self.schema_sha256, "schema_sha256"),
        )
        object.__setattr__(
            self,
            "records_digest",
            validate_sha256(self.records_digest, "records_digest"),
        )
        object.__setattr__(self, "item_count", non_negative_int(self.item_count, "item_count"))
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationSubmissionStatus, "status"),
        )
        object.__setattr__(
            self,
            "data_source",
            coerce_enum(self.data_source, DataSource, "data_source"),
        )
        records = tuple(self.records)
        for item in records:
            if not isinstance(item, AnnotationRecord):
                raise DomainValidationError("records 只能包含 AnnotationRecord")
            if item.project_id != self.project_id or item.submission_id != self.submission_id:
                raise DomainValidationError("记录必须属于当前项目与提交")
        object.__setattr__(self, "records", records)
        refs = tuple(self.record_refs) or tuple(item.annotation_id for item in records)
        object.__setattr__(self, "record_refs", refs)
        if records:
            if self.item_count != len(records):
                raise DomainValidationError("item_count 必须等于记录数量")
            expected = compute_records_digest(records)
            if expected != self.records_digest:
                raise RecordDigestMismatchError("records_digest 与记录内容不一致")
        for name in ("review_id", "arbitration_id", "supersedes_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        for name in ("submitted_at", "decided_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "reason",
            optional_text(self.reason, "reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def is_editable(self) -> bool:
        return self.status is AnnotationSubmissionStatus.DRAFT

    @property
    def is_immutable(self) -> bool:
        return not self.is_editable

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationSubmission:
        action_name = validate_action_name(action, family="submission")
        target = submission_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("submission", action_name) and cleaned_reason is None:
            raise DomainValidationError("该提交动作必须填写原因")
        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if target is AnnotationSubmissionStatus.SUBMITTED:
            changes["submitted_at"] = self.submitted_at or occurred_at
        if target in {
            AnnotationSubmissionStatus.APPROVED,
            AnnotationSubmissionStatus.REJECTED,
            AnnotationSubmissionStatus.ESCALATED,
            AnnotationSubmissionStatus.RESOLVED_APPROVED,
            AnnotationSubmissionStatus.RESOLVED_REJECTED,
            AnnotationSubmissionStatus.RESOLVED_REWORK,
            AnnotationSubmissionStatus.REWORK_REQUIRED,
            AnnotationSubmissionStatus.WITHDRAWN,
            AnnotationSubmissionStatus.SUPERSEDED,
        }:
            changes["decided_at"] = self.decided_at or occurred_at
        if action_name in {
            AnnotationSubmissionAction.WITHDRAW.value,
            AnnotationSubmissionAction.REJECT.value,
            AnnotationSubmissionAction.ESCALATE.value,
            AnnotationSubmissionAction.RESOLVE_REJECTED.value,
            AnnotationSubmissionAction.RESOLVE_REWORK.value,
            AnnotationSubmissionAction.REQUIRE_REWORK.value,
            AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value,
        }:
            changes["reason"] = cleaned_reason
        return replace(self, **changes)

    def assert_content_immutable(self, candidate: AnnotationSubmission) -> None:
        if self.is_immutable and (
            self.assignment_id != candidate.assignment_id
            or self.submission_version != candidate.submission_version
            or self.schema_id != candidate.schema_id
            or self.schema_version != candidate.schema_version
            or self.schema_sha256 != candidate.schema_sha256
            or self.records_digest != candidate.records_digest
            or self.item_count != candidate.item_count
            or records_payload(self.records) != records_payload(candidate.records)
        ):
            raise ResourceImmutableError("已提交版本不可原地修改")


def _require_type(definition: LabelDefinition, value: object) -> None:
    label_type = definition.label_type
    spec = definition.value_spec
    if label_type is AnnotationLabelType.ENUM:
        values = spec.get("enum_values") or ()
        if not isinstance(value, str) or value not in values:
            raise LabelValueInvalidError("ENUM 标签值不在允许枚举内")
    elif label_type is AnnotationLabelType.MULTI_ENUM:
        values = spec.get("enum_values") or ()
        if not isinstance(value, list | tuple):
            raise LabelValueInvalidError("MULTI_ENUM 标签值必须是字符串列表")
        cleaned = list(value)
        if not cleaned or len(set(cleaned)) != len(cleaned):
            raise LabelValueInvalidError("MULTI_ENUM 标签值不得为空或重复")
        if any(not isinstance(item, str) or item not in values for item in cleaned):
            raise LabelValueInvalidError("MULTI_ENUM 标签值不在允许枚举内")
    elif label_type is AnnotationLabelType.BOOLEAN:
        if not isinstance(value, bool):
            raise LabelValueInvalidError("BOOLEAN 标签值必须是布尔值")
    elif label_type is AnnotationLabelType.NUMBER:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise LabelValueInvalidError("NUMBER 标签值必须是数字")
        number = float(value)
        if spec.get("min_value") is not None and number < float(spec["min_value"]):
            raise LabelValueInvalidError("NUMBER 标签值小于最小值")
        if spec.get("max_value") is not None and number > float(spec["max_value"]):
            raise LabelValueInvalidError("NUMBER 标签值大于最大值")
    elif label_type in {AnnotationLabelType.STRING, AnnotationLabelType.FREE_TEXT}:
        if not isinstance(value, str):
            raise LabelValueInvalidError("字符串标签值必须是字符串")
        max_length = spec.get("max_length")
        if max_length is not None and len(value) > int(max_length):
            raise LabelValueInvalidError("字符串标签值超过最大长度")
    elif label_type in {
        AnnotationLabelType.TIME_RANGE,
        AnnotationLabelType.ACTION_SEGMENT,
    }:
        _require_time_value(spec, value)
    elif label_type is AnnotationLabelType.OBJECT_TRACK:
        if not isinstance(value, list | tuple) or not value:
            raise LabelValueInvalidError("OBJECT_TRACK 标签值必须是非空列表")
        for track in value:
            if not isinstance(track, Mapping):
                raise LabelValueInvalidError("OBJECT_TRACK 每个元素必须是对象")
            _require_time_value(spec, track)
    elif label_type is AnnotationLabelType.JSON:
        _validated_json(value)


def _require_time_value(spec: Mapping[str, object], value: object) -> None:
    if not isinstance(value, Mapping):
        raise LabelValueInvalidError("时间片段标签值必须是对象")
    start, end = value.get("start_time_ns"), value.get("end_time_ns")
    check_ns_range(start, end, "label.time_range")
    duration = int(end) - int(start)  # type: ignore[arg-type]
    min_duration = spec.get("min_duration_ns")
    max_duration = spec.get("max_duration_ns")
    if min_duration is not None and duration < int(min_duration):
        raise LabelValueInvalidError("时间片段短于最小允许时长")
    if max_duration is not None and duration > int(max_duration):
        raise LabelValueInvalidError("时间片段超过最大允许时长")
    domain = value.get("time_domain", "UTC")
    if domain not in _TIME_DOMAIN_VALUES:
        raise LabelValueInvalidError("时间片段 time_domain 取值非法")


def validate_record_labels(
    record: AnnotationRecord,
    definitions: tuple[LabelDefinition, ...],
    *,
    require_confirmed: bool = True,
) -> None:
    """按 Schema 快照校验记录标签：未知键、类型、枚举、范围与必填。

    ## 为什么 AI 置信度的强制放在这里，而不是 ``LabelValue.__post_init__``

    ``__post_init__`` 是所有构造路径的必经之地，也包括**读库重建**
    （``_normalize_labels`` 把 JSONB 读出来重新构造 ``LabelValue``）。
    在那里加强制，历史库里那些 ``source=AI_ASSISTED`` 但没写 ``confidence``
    的记录会直接构造失败——整条标注记录读不出来，连带提交也打不开。
    那是用"修一个新约束"换"历史数据全不可读"，代价完全不成比例。

    放在这里则只在**提交校验**路径生效：新提交必须合规，历史数据照常可读。
    这是"约束只对新数据生效"的刻意选择，不是遗漏。

    审计背景（N4 / QUA-005）：``source=AI_ASSISTED`` 与 ``confidence`` 之间
    此前没有任何强制关系——AI 来源可以不带置信度，于是「AI 给的标签有多可信」
    在数据层面无从回答，N7 的采纳统计只能退化为计数。
    """
    by_key = {definition.label_key: definition for definition in definitions}
    seen: set[str] = set()
    for label in record.labels:
        definition = by_key.get(label.label_key)
        if definition is None:
            raise UnknownLabelError(f"标签 {label.label_key} 未在 Schema 中定义")
        seen.add(label.label_key)
        if require_confirmed and label.label_status is not LabelValueStatus.CONFIRMED:
            raise SubmissionIncompleteError("提交前标签值必须为 CONFIRMED")
        if label.source is LabelValueSource.AI_ASSISTED and label.confidence is None:
            # AI 来源必须带置信度：不然"无法证明达标"与"未提供依据"无法区分，
            # 质量规则与采纳统计都会失去判断基础。
            raise LabelValueInvalidError(f"AI_ASSISTED 标签 {label.label_key} 必须提供 confidence")
        _require_type(definition, label.value)
    missing = [
        definition.label_key
        for definition in definitions
        if definition.required and definition.label_key not in seen
    ]
    if missing:
        raise SubmissionIncompleteError("提交缺少必填标签")


__all__ = [
    "AnnotationRecord",
    "AnnotationSubmission",
    "DECIDED_SUBMISSION_STATUSES",
    "EvidenceRef",
    "LabelValue",
    "VISIBLE_DRAFT_STATUS",
    "compute_record_sha256",
    "compute_records_digest",
    "record_payload",
    "records_payload",
    "submission_already_decided",
    "submission_visible_to",
    "validate_record_labels",
]
