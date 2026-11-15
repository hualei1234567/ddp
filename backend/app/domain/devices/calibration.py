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
from app.domain.devices.errors import DomainValidationError, FourEyesViolationError
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


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    """标定配置版本：定义某类目标需要哪些标定及选择规则。"""

    profile_id: UUID
    project_id: UUID
    name: str
    scope: CalibrationScope | str
    target_type: CalibrationTargetType | str
    version: int
    schema_version: str
    required_types: tuple[CalibrationType | str, ...] = ()
    selection_rule: Mapping[str, object] = field(default_factory=dict)
    # ADR-0020（G-06）：scope=TARGET 时指向具体目标；TYPE/SYSTEM 时必须为空。
    target_id: UUID | None = None

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
        object.__setattr__(
            self,
            "schema_version",
            normalized_text(
                self.schema_version,
                "schema_version",
                max_length=VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
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
        """VALIDATED→ACTIVE；有效期不合法时拒绝，避免过期标定被激活。"""
        current = aware_datetime(now or datetime.now(UTC), "now")
        if self.valid_from > current:
            raise DomainValidationError("标定尚未生效，不能激活")
        if self.valid_to is not None and self.valid_to <= current:
            raise DomainValidationError("标定已过期，不能激活")
        return replace(self, status=self._target_status(CalibrationStatus.ACTIVE))

    def supersede(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.SUPERSEDED))

    def revoke(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.REVOKED))

    def expire(self) -> Self:
        return replace(self, status=self._target_status(CalibrationStatus.EXPIRED))
