"""标定配置与标定记录领域实体。

标定遵循 DDP-101 §3.2 与 Canonical 草案 §6：
- 执行人与校验人必须分离（四眼原则）；
- 外参/手眼必须声明父子坐标系；
- 状态只能通过具名动作迁移；
- 激活时由仓储在同一事务内把同目标旧 ACTIVE 记录转为 SUPERSEDED。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from app.domain.devices.enums import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
)
from app.domain.devices.errors import (
    CalibrationInvalidError,
    DomainValidationError,
    FourEyesViolationError,
)
from app.domain.devices.parameter_schema import validate_parameters
from app.domain.devices.state_machines import assert_calibration_transition
from app.domain.devices.validation import (
    CHECKSUM_MAX_LENGTH,
    FRAME_MAX_LENGTH,
    METHOD_MAX_LENGTH,
    NAME_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    VERSION_MAX_LENGTH,
    aware_datetime,
    coerce_enum,
    frozen_mapping,
    normalized_text,
    optional_text,
    positive_int,
    uuid_tuple,
)

_VALIDATED_STATUSES = frozenset(
    {
        CalibrationStatus.VALIDATED,
        CalibrationStatus.ACTIVE,
        CalibrationStatus.SUPERSEDED,
        CalibrationStatus.REVOKED,
        CalibrationStatus.EXPIRED,
    }
)
_FRAME_REQUIRED_TYPES = frozenset(
    {
        CalibrationType.EXTRINSICS,
        CalibrationType.HAND_EYE,
    }
)


def _signature_value(value: object) -> object:
    """把内容字段归一成可比较的不可变形状。

    ``parameters`` / ``uncertainty`` 是只读 Mapping，``evidence_asset_ids``
    是 tuple：直接用 ``==`` 比较本来可以，但嵌套 dict 与 list 的等价性
    依赖插入顺序之外的细节；这里统一转成排序后的元组，让"同一内容"
    在两次写入后一定得到同一指纹。
    """
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _signature_value(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_signature_value(item) for item in value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "value"):  # StrEnum
        return value.value
    return value


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    """标定配置版本：定义某类目标需要哪些标定及选择规则。"""

    profile_id: UUID
    project_id: UUID
    name: str
    scope: CalibrationScope | str
    target_type: CalibrationTargetType | str
    # ADR-0032（G-03）：业务版本号，**不是**乐观锁。与 CalibrationRecord.version
    # 的同名字段语义不同，故显式命名为 profile_version，避免读写时张冠李戴。
    profile_version: int
    schema_version: str
    required_types: tuple[CalibrationType | str, ...] = ()
    selection_rule: Mapping[str, object] = field(default_factory=dict)
    # ADR-0020（G-06）：仅 Profile 绑定具体目标；SYSTEM 作用域必须为空。
    # 注意：标定**记录**的 SYSTEM 目标口径相反（``target_id == project_id``），
    # 那是 ``calibration_target_refs`` 复合外键的要求，见 ADR-0020 §2.3。
    target_id: UUID | None = None
    # 配置版本链：新版本指向它替代的那一版。只校验不落库时，"谁替代了谁"
    # 在库里不存在，既画不出链路，也拦不住同一父版本被两条分支各自继承（分叉）。
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "scope",
            coerce_enum(self.scope, CalibrationScope, "scope"),
        )
        object.__setattr__(
            self,
            "target_type",
            coerce_enum(self.target_type, CalibrationTargetType, "target_type"),
        )
        if self.supersedes_id is not None:
            if not isinstance(self.supersedes_id, UUID):
                raise DomainValidationError("supersedes_id 必须是 UUID")
            if self.supersedes_id == self.profile_id:
                raise DomainValidationError("supersedes_id 不能指向自身")
        object.__setattr__(
            self,
            "schema_version",
            normalized_text(
                self.schema_version,
                "schema_version",
                max_length=VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "profile_version",
            positive_int(self.profile_version, "profile_version"),
        )
        object.__setattr__(
            self,
            "required_types",
            tuple(
                coerce_enum(item, CalibrationType, "required_types") for item in self.required_types
            ),
        )
        object.__setattr__(
            self,
            "selection_rule",
            frozen_mapping(self.selection_rule, "selection_rule"),
        )
        # ADR-0020（G-06）：SYSTEM 作用域是项目级模板，不应绑定具体目标；
        # 其余作用域可绑定，但绑定后必须与该作用域的类型相符（由应用层校验目标存在性）。
        if self.scope is CalibrationScope.SYSTEM and self.target_id is not None:
            raise DomainValidationError("scope=SYSTEM 是项目级模板，不能绑定 target_id")


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    """标定记录聚合根；``parameters`` 的具体 Schema 由应用层按类型校验。"""

    calibration_id: UUID
    project_id: UUID
    profile_id: UUID
    scope: CalibrationScope | str
    target_type: str
    target_id: UUID
    calibration_type: CalibrationType | str
    method: str
    parameters: Mapping[str, object]
    valid_from: datetime
    evidence_asset_ids: tuple[UUID, ...]
    checksum: str
    performed_by: UUID
    status: CalibrationStatus | str = CalibrationStatus.DRAFT
    valid_to: datetime | None = None
    uncertainty: Mapping[str, object] = field(default_factory=dict)
    parent_frame: str | None = None
    child_frame: str | None = None
    source: str | None = None
    validated_by: UUID | None = None
    validated_at: datetime | None = None
    supersedes_id: UUID | None = None
    # ADR-0020（G-04）：version 是乐观锁；calibration_version 是业务修订号。
    # 两者语义不同：状态迁移只动 version，内容变更才动 calibration_version。
    version: int = 1
    calibration_version: int = 1
    # ADR-0020（G-05）：创建时的 Profile 快照，保证事后可复现。
    profile_version: int | None = None
    profile_schema_version: str | None = None
    profile_snapshot: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scope",
            coerce_enum(self.scope, CalibrationScope, "scope"),
        )
        object.__setattr__(
            self,
            "target_type",
            coerce_enum(self.target_type, CalibrationTargetType, "target_type"),
        )
        object.__setattr__(
            self,
            "calibration_type",
            coerce_enum(self.calibration_type, CalibrationType, "calibration_type"),
        )
        object.__setattr__(
            self,
            "method",
            normalized_text(self.method, "method", max_length=METHOD_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "checksum",
            normalized_text(
                self.checksum,
                "checksum",
                max_length=CHECKSUM_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "source",
            optional_text(self.source, "source", max_length=SOURCE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, CalibrationStatus, "status"),
        )
        object.__setattr__(
            self,
            "parameters",
            frozen_mapping(self.parameters, "parameters"),
        )
        object.__setattr__(
            self,
            "uncertainty",
            frozen_mapping(self.uncertainty, "uncertainty"),
        )
        object.__setattr__(
            self,
            "evidence_asset_ids",
            uuid_tuple(self.evidence_asset_ids, "evidence_asset_ids"),
        )
        # DDP-101 §3.2：DRAFT→SUBMITTED 要求证据已登记；
        # 非 DRAFT 快照若没有证据，说明状态被绕过迁移直接构造，必须拒绝。
        if self.status is not CalibrationStatus.DRAFT and not self.evidence_asset_ids:
            raise DomainValidationError(f"{self.status.value} 状态必须登记证据")
        object.__setattr__(self, "valid_from", aware_datetime(self.valid_from, "valid_from"))
        if self.valid_to is not None:
            valid_to = aware_datetime(self.valid_to, "valid_to")
            if valid_to <= self.valid_from:
                raise DomainValidationError("valid_to 必须晚于 valid_from")
            object.__setattr__(self, "valid_to", valid_to)
        self._validate_frames()
        self._validate_validator()
        if self.validated_at is not None:
            object.__setattr__(
                self,
                "validated_at",
                aware_datetime(self.validated_at, "validated_at"),
            )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        # ADR-0020（G-04/G-05）：业务修订号与 Profile 快照。
        object.__setattr__(
            self,
            "calibration_version",
            positive_int(self.calibration_version, "calibration_version"),
        )
        if self.profile_version is not None:
            object.__setattr__(
                self,
                "profile_version",
                positive_int(self.profile_version, "profile_version"),
            )
        object.__setattr__(
            self,
            "profile_snapshot",
            frozen_mapping(self.profile_snapshot, "profile_snapshot"),
        )
        # G-19：参数形状由契约 schema 决定。非法单位、缺帧、未知键
        # 必须在进入 DRAFT 前就被拦住，而不是等到下游解析时才炸。
        validate_parameters(self.calibration_type, self.parameters)

    def _validate_frames(self) -> None:
        parent = optional_text(
            self.parent_frame,
            "parent_frame",
            max_length=FRAME_MAX_LENGTH,
        )
        child = optional_text(
            self.child_frame,
            "child_frame",
            max_length=FRAME_MAX_LENGTH,
        )
        object.__setattr__(self, "parent_frame", parent)
        object.__setattr__(self, "child_frame", child)
        if (parent is None) != (child is None):
            raise DomainValidationError("parent_frame 与 child_frame 必须同时提供")
        if self.calibration_type in _FRAME_REQUIRED_TYPES and parent is None:
            raise DomainValidationError(
                f"{self.calibration_type.value} 标定必须声明 parent_frame 与 child_frame"
            )

    def _validate_validator(self) -> None:
        if self.validated_by is not None:
            if not isinstance(self.validated_by, UUID):
                raise DomainValidationError("validated_by 必须是 UUID")
            if self.validated_by == self.performed_by:
                raise FourEyesViolationError("标定执行人与校验人必须分离")
        if self.status in _VALIDATED_STATUSES:
            if self.validated_by is None or self.validated_at is None:
                raise DomainValidationError(
                    f"{self.status.value} 状态必须记录 validated_by 与 validated_at"
                )

    def _target_status(self, target: CalibrationStatus) -> CalibrationStatus:
        return assert_calibration_transition(self.status, target)

    # ADR-0020（G-04/G-05）：需要比较"哪些字段算内容"的地方只有一处权威定义。
    # checksum 是内容的派生值，not 内容本身；version 是乐观锁；status 是状态机；
    # calibration_version 是修订号；三个 profile_* 是快照。
    _CONTENT_FIELDS = (
        "profile_id",
        "scope",
        "target_type",
        "target_id",
        "calibration_type",
        "method",
        "parameters",
        "parent_frame",
        "child_frame",
        "valid_from",
        "valid_to",
        "evidence_asset_ids",
        "source",
        "uncertainty",
    )

    _SNAPSHOT_FIELDS = (
        "profile_version",
        "profile_schema_version",
        "profile_snapshot",
    )

    def with_content_changes(self, **changes: object) -> Self:
        """应用草稿内容变更；**实质改动**时递增 ``calibration_version``。

        为什么在 Domain 里判定而不是让调用方传"要不要 +1"：
        调用方一旦可以自由决定，就会出现"两次内容 PATCH 版本不动"或
        "状态迁移误加版本"两类漂移，版本号再次失去意义（G-04）。
        判定规则唯一：内容字段的**值**变了才 +1；把值改成相同值不算改动。
        """
        candidate = replace(self, **changes)
        if self._content_signature(candidate) != self._content_signature(self):
            return replace(candidate, calibration_version=self.calibration_version + 1)
        return candidate

    def with_profile_snapshot(
        self,
        *,
        profile_version: int,
        profile_schema_version: str,
        profile_snapshot: Mapping[str, object],
    ) -> Self:
        """整体替换 Profile 快照三字段（G-05：换 profile_id 必须换快照）。"""
        return replace(
            self,
            profile_version=positive_int(profile_version, "profile_version"),
            profile_schema_version=normalized_text(
                profile_schema_version,
                "profile_schema_version",
                max_length=VERSION_MAX_LENGTH,
            ),
            profile_snapshot=profile_snapshot,
        )

    def _content_signature(self, record: CalibrationRecord) -> tuple[object, ...]:
        """内容指纹：只取内容字段，用于判定"是否实质改动"。"""
        return tuple(_signature_value(getattr(record, name)) for name in self._CONTENT_FIELDS)

    def submit(self) -> Self:
        # DDP-101 §3.2：DRAFT→SUBMITTED 要求证据已登记；草案本身允许暂无证据。
        if not self.evidence_asset_ids:
            raise DomainValidationError("标定提交前必须登记证据")
        return replace(self, status=self._target_status(CalibrationStatus.SUBMITTED))

    def validate(self, validator_id: UUID, validated_at: datetime) -> Self:
        if not isinstance(validator_id, UUID):
            raise DomainValidationError("validator_id 必须是 UUID")
        if validator_id == self.performed_by:
            raise FourEyesViolationError("标定执行人与校验人必须分离")
        status = self._target_status(CalibrationStatus.VALIDATED)
        return replace(
            self,
            status=status,
            validated_by=validator_id,
            validated_at=aware_datetime(validated_at, "validated_at"),
        )

    def reject(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.REJECTED))

    def reopen(self) -> Self:
        return replace(
            self,
            status=self._target_status(CalibrationStatus.DRAFT),
            validated_by=None,
            validated_at=None,
        )

    def activate(self, *, now: datetime | None = None) -> Self:
        """VALIDATED→ACTIVE；有效期不合法时拒绝，避免过期标定被激活。

        有效期窗口不允许是 ``validation_failed``：请求形状完全合法，
        拒绝原因是**这条标定的数据**在此时点不可用。契约把
        VALIDATED→ACTIVE 的这类拒绝登记为 ``calibration_invalid``（G-20）。
        """
        current = aware_datetime(now or datetime.now(UTC), "now")
        if self.valid_from > current:
            raise CalibrationInvalidError(
                "标定尚未生效，不能激活",
                details={"reason": "not_yet_valid", "field": "valid_from"},
            )
        if self.valid_to is not None and self.valid_to <= current:
            raise CalibrationInvalidError(
                "标定已过期，不能激活",
                details={"reason": "expired", "field": "valid_to"},
            )
        return replace(self, status=self._target_status(CalibrationStatus.ACTIVE))

    def supersede(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.SUPERSEDED))

    def revoke(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.REVOKED))

    def expire(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.EXPIRED))
