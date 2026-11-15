"""标定记录用例：项目作用域查询、草稿写入与状态动作。

校验和由服务端根据固定字段重新计算，客户端提交的 checksum 不接受。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.devices.commands import NewCalibration
from app.application.devices.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.devices.ports import (
    CalibrationProfileRepository,
    CalibrationRecordRepository,
    UnitOfWork,
)
from app.domain.devices.calibration import CalibrationRecord
from app.domain.devices.enums import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
)
from app.domain.devices.errors import (
    CalibrationProfileInvalidError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope

_PATCHABLE_FIELDS = frozenset(
    {
        "profile_id",
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
    }
)

_CHECKSUM_FIELDS = (
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
    "uncertainty",
    "performed_by",
    "supersedes_id",
)


class CalibrationUseCases:
    def __init__(
        self,
        calibrations: CalibrationRecordRepository,
        profiles: CalibrationProfileRepository,
        uow: UnitOfWork,
    ) -> None:
        self._calibrations = calibrations
        self._profiles = profiles
        self._uow = uow

    def list_calibrations(
        self,
        scope: ProjectScope,
        *,
        target_type: str | None = None,
        target_id: UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CalibrationRecord]:
        bounded_limit = normalize_limit(limit)
        target_filter = _target_type(target_type)
        items = self._calibrations.list(
            scope,
            target_type=target_filter.value if target_filter else None,
            target_id=target_id,
            status=_calibration_status(status),
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        return build_page(
            items,
            limit=bounded_limit,
            id_of=lambda item: item.calibration_id,
        )

    def get_calibration(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
    ) -> CalibrationRecord:
        record = self._calibrations.get(scope, calibration_id)
        if record is None:
            raise ResourceNotFoundError("标定", calibration_id)
        return record

    def create_calibration(
        self,
        scope: ProjectScope,
        command: NewCalibration,
    ) -> CalibrationRecord:
        target_type = _target_type(command.target_type)
        if target_type is None:
            raise DomainValidationError("target_type 不能为空")
        profile = self._validate_profile(scope, command.profile_id, target_type)
        record = CalibrationRecord(
            calibration_id=uuid4(),
            project_id=scope.project_id,
            profile_id=command.profile_id,
            scope=CalibrationScope(target_type.value),
            target_type=target_type,
            target_id=command.target_id,
            calibration_type=command.calibration_type,
            method=command.method,
            parameters=command.parameters,
            valid_from=command.valid_from,
            valid_to=command.valid_to,
            parent_frame=command.parent_frame,
            child_frame=command.child_frame,
            status=CalibrationStatus.DRAFT,
            evidence_asset_ids=command.evidence_asset_ids,
            source=command.source,
            checksum="sha256:pending",
            uncertainty=command.uncertainty,
            performed_by=scope.actor_id,
            # ADR-0020（G-05）：固化创建时的 Profile 快照。
            # 只存版本号不够——Profile 那一行将来会被改，快照是唯一能复现的依据。
            profile_version=profile.version,
            profile_schema_version=profile.schema_version,
            profile_snapshot={
                "required_types": [str(item) for item in profile.required_types],
                "selection_rule": dict(profile.selection_rule),
                "target_type": str(profile.target_type),
            },
        )
        record = replace(record, checksum=_compute_checksum(record))
        created = self._calibrations.add(scope, record)
        self._uow.commit()
        return created

    def update_calibration(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> CalibrationRecord:
        _ensure_patchable(changes)
        current = self.get_calibration(scope, calibration_id)
        if current.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        candidate = replace(current, **dict(changes))
        if "target_type" in changes:
            candidate = replace(
                candidate,
                scope=CalibrationScope(candidate.target_type.value),
            )
        if "profile_id" in changes or "target_type" in changes:
            self._validate_profile(scope, candidate.profile_id, candidate.target_type)
        if candidate.status is CalibrationStatus.DRAFT:
            candidate = replace(candidate, checksum=_compute_checksum(candidate))
        updated = self._calibrations.save(
            scope,
            candidate,
            expected_version=expected_version,
        )
        self._uow.commit()
        return updated

    def submit(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.submit(
                scope, current, expected_version=version
            ),
        )

    def validate(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        now = datetime.now(UTC)
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.validate(
                scope,
                current,
                scope.actor_id,
                now,
                expected_version=version,
            ),
        )

    def reject(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.reject(
                scope, current, expected_version=version
            ),
        )

    def activate(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        now = datetime.now(UTC)
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.activate(
                scope,
                current,
                expected_version=version,
                now=now,
            ),
        )

    def revoke(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.revoke(
                scope, current, expected_version=version
            ),
        )

    def expire(self, scope: ProjectScope, calibration_id: UUID, *, expected_version: int):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            lambda current, version: self._calibrations.expire(
                scope, current, expected_version=version
            ),
        )

    def _run(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        expected_version: int,
        handler: Callable[[CalibrationRecord, int], CalibrationRecord],
    ) -> CalibrationRecord:
        current = self.get_calibration(scope, calibration_id)
        if current.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        updated = handler(current, expected_version)
        self._uow.commit()
        return updated

    def _validate_profile(
        self,
        scope: ProjectScope,
        profile_id: UUID,
        target_type: CalibrationTargetType,
    ):
        """校验并返回 Profile；调用方需要它来写快照（ADR-0020 / G-05）。"""
        profile = self._profiles.get(scope, profile_id)
        if profile is None:
            raise CalibrationProfileInvalidError("标定配置不存在或不属于当前项目")
        if profile.target_type.value != target_type.value:
            raise CalibrationProfileInvalidError("标定配置 target_type 与记录不一致")
        return profile


def _ensure_patchable(changes: Mapping[str, object]) -> None:
    if set(changes) - _PATCHABLE_FIELDS:
        raise DomainValidationError("请求包含不允许修改的字段")


def _target_type(value: str | None) -> CalibrationTargetType | None:
    if value is None:
        return None
    try:
        return CalibrationTargetType(value)
    except ValueError as exc:
        raise DomainValidationError("target_type 取值非法") from exc


def _calibration_status(value: str | None) -> CalibrationStatus | None:
    if value is None:
        return None
    try:
        return CalibrationStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _compute_checksum(record: CalibrationRecord) -> str:
    payload = {name: _json_value(getattr(record, name)) for name in _CHECKSUM_FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _json_value(value: object) -> object:
    if isinstance(value, CalibrationType | CalibrationScope | CalibrationTargetType):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
