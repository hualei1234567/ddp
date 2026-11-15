"""标定记录用例：项目作用域查询、草稿写入与状态动作。

校验和由服务端按 RFC 8785 JCS 对固定字段重算（G-15），客户端提交的 checksum
一律忽略；``submit`` / ``validate`` / ``activate`` 三个状态命令前都会重算比对，
任何绕过用例层的直写篡改都会在这里暴露。

留痕（G-11）：创建与全部状态动作都经 :func:`record_change` 同事务写
``audit_logs`` 与 ``outbox_events``，与 devices / sensors / robots 一致。
此前本域没有 audit/outbox 依赖，标定改了什么只能靠翻表推断。

事件类型（R-13）：一律用模块级常量或字面量映射表，**不使用 f-string**。
``scripts/check_outbox_events.py`` 用 AST 提取事件名，f-string 抓不到，
新增事件会在 CI 里静默逃过归类校验。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import record_change
from app.application.devices.commands import NewCalibration
from app.application.devices.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.devices.ports import (
    AuditRepository,
    CalibrationProfileRepository,
    CalibrationRecordRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.common.canonical_json import CanonicalJSONError, canonical_json, sha256_text
from app.domain.common.ids import new_id
from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
)
from app.domain.devices.errors import (
    CalibrationChecksumMismatchError,
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

# R-13：事件类型必须是模块级常量或字面量映射表，禁止 f-string。
# ``scripts/check_outbox_events.py`` 用 AST 提取，f-string 会静默逃过归类校验。
CALIBRATION_CREATED_EVENT = "calibration.created"
CALIBRATION_UPDATED_EVENT = "calibration.updated"

# 动作 → 事件类型。字典值写全字面量，便于 AST 与人工审计同时读懂。
_CALIBRATION_ACTION_EVENTS: dict[str, str] = {
    "submit": "calibration.submitted",
    "validate": "calibration.validated",
    "reject": "calibration.rejected",
    "activate": "calibration.activated",
    "revoke": "calibration.revoked",
    "expire": "calibration.expired",
}


class CalibrationUseCases:
    def __init__(
        self,
        calibrations: CalibrationRecordRepository,
        profiles: CalibrationProfileRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        outbox: OutboxRepository,
    ) -> None:
        self._calibrations = calibrations
        self._profiles = profiles
        self._uow = uow
        self._audit = audit
        self._outbox = outbox

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
        *,
        request_id: str | None = None,
    ) -> CalibrationRecord:
        target_type = _target_type(command.target_type)
        if target_type is None:
            raise DomainValidationError("target_type 不能为空")
        profile = self._validate_profile(scope, command.profile_id, target_type)
        now = datetime.now(UTC)
        record = CalibrationRecord(
            calibration_id=new_id(),
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
            profile_version=profile.profile_version,
            profile_schema_version=profile.schema_version,
            profile_snapshot=_profile_snapshot(profile),
        )
        record = replace(record, checksum=_compute_checksum(record))
        created = self._calibrations.add(scope, record)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="calibration",
            resource_id=created.calibration_id,
            action="create",
            from_status=None,
            to_status=_enum_value(created.status),
            reason=None,
            request_id=request_id,
            details={
                "profile_id": str(created.profile_id),
                "profile_version": created.profile_version,
                "target_type": _enum_value(created.target_type),
                "target_id": str(created.target_id),
                "calibration_type": _enum_value(created.calibration_type),
                "calibration_version": created.calibration_version,
            },
            event_type=CALIBRATION_CREATED_EVENT,
            event_payload={
                "calibration_id": str(created.calibration_id),
                "status": _enum_value(created.status),
                "calibration_version": created.calibration_version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def update_calibration(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> CalibrationRecord:
        """编辑草稿。

        两件事必须一起做，否则记录会自相矛盾：
        - G-04：内容实质变化时递增 ``calibration_version``（状态迁移不动它）；
        - G-05：换 ``profile_id`` 时重写快照三字段，让"指向哪个 Profile"与
          "按哪个 Profile 复现"始终一致。
        """
        _ensure_patchable(changes)
        current = self.get_calibration(scope, calibration_id)
        if current.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        candidate = current.with_content_changes(**dict(changes))
        if "target_type" in changes:
            candidate = replace(
                candidate,
                scope=CalibrationScope(candidate.target_type.value),
            )
        profile: CalibrationProfile | None = None
        if "profile_id" in changes or "target_type" in changes:
            profile = self._validate_profile(
                scope,
                candidate.profile_id,
                candidate.target_type,
            )
        # 只要 profile_id 变了就必须重写快照——校验通过不代表快照还对得上。
        if "profile_id" in changes and profile is not None:
            candidate = candidate.with_profile_snapshot(
                profile_version=profile.profile_version,
                profile_schema_version=profile.schema_version,
                profile_snapshot=_profile_snapshot(profile),
            )
        if candidate.status is CalibrationStatus.DRAFT:
            candidate = replace(candidate, checksum=_compute_checksum(candidate))
        updated = self._calibrations.save(
            scope,
            candidate,
            expected_version=expected_version,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="calibration",
            resource_id=updated.calibration_id,
            action="update",
            from_status=_enum_value(current.status),
            to_status=_enum_value(updated.status),
            reason=None,
            request_id=request_id,
            details={
                "changed_fields": sorted(changes),
                "calibration_version": updated.calibration_version,
                "profile_id": str(updated.profile_id),
                "profile_version": updated.profile_version,
            },
            event_type=CALIBRATION_UPDATED_EVENT,
            event_payload={
                "calibration_id": str(updated.calibration_id),
                "status": _enum_value(updated.status),
                "calibration_version": updated.calibration_version,
            },
            occurred_at=datetime.now(UTC),
        )
        self._uow.commit()
        return updated

    def submit(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "submit",
            lambda current, version: self._calibrations.submit(
                scope, current, expected_version=version
            ),
            request_id=request_id,
            verify_checksum=True,
        )

    def validate(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        now = datetime.now(UTC)
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "validate",
            lambda current, version: self._calibrations.validate(
                scope,
                current,
                scope.actor_id,
                now,
                expected_version=version,
            ),
            request_id=request_id,
            verify_checksum=True,
        )

    def reject(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "reject",
            lambda current, version: self._calibrations.reject(
                scope, current, expected_version=version
            ),
            request_id=request_id,
        )

    def activate(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        now = datetime.now(UTC)
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "activate",
            lambda current, version: self._calibrations.activate(
                scope,
                current,
                expected_version=version,
                now=now,
            ),
            request_id=request_id,
            verify_checksum=True,
        )

    def revoke(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "revoke",
            lambda current, version: self._calibrations.revoke(
                scope, current, expected_version=version
            ),
            request_id=request_id,
        )

    def expire(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        *,
        expected_version: int,
        request_id: str | None = None,
    ):
        return self._run(
            scope,
            calibration_id,
            expected_version,
            "expire",
            lambda current, version: self._calibrations.expire(
                scope, current, expected_version=version
            ),
            request_id=request_id,
        )

    def _run(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
        expected_version: int,
        action: str,
        handler: Callable[[CalibrationRecord, int], CalibrationRecord],
        *,
        request_id: str | None = None,
        verify_checksum: bool = False,
    ) -> CalibrationRecord:
        current = self.get_calibration(scope, calibration_id)
        if current.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        if verify_checksum:
            _assert_checksum_matches(current)
        updated = handler(current, expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="calibration",
            resource_id=updated.calibration_id,
            action=action,
            from_status=_enum_value(current.status),
            to_status=_enum_value(updated.status),
            reason=None,
            request_id=request_id,
            details={
                # 状态迁移不动业务修订号，这里如实记录，便于对账（G-04）。
                "calibration_version": updated.calibration_version,
                "profile_id": str(updated.profile_id),
                "profile_version": updated.profile_version,
            },
            event_type=_CALIBRATION_ACTION_EVENTS[action],
            event_payload={
                "calibration_id": str(updated.calibration_id),
                "from_status": _enum_value(current.status),
                "to_status": _enum_value(updated.status),
                "action": action,
            },
            occurred_at=datetime.now(UTC),
        )
        self._uow.commit()
        return updated

    def _validate_profile(
        self,
        scope: ProjectScope,
        profile_id: UUID,
        target_type: CalibrationTargetType,
    ) -> CalibrationProfile:
        """校验并返回 Profile；调用方需要它来写快照（ADR-0020 / G-05）。"""
        profile = self._profiles.get(scope, profile_id)
        if profile is None:
            raise CalibrationProfileInvalidError("标定配置不存在或不属于当前项目")
        if profile.target_type.value != target_type.value:
            raise CalibrationProfileInvalidError("标定配置 target_type 与记录不一致")
        return profile


def _profile_snapshot(profile: CalibrationProfile) -> dict[str, object]:
    """Profile 内容副本；创建与换 profile 时共用，避免两处写法漂移（G-05）。"""
    return {
        "required_types": [_enum_value(item) for item in profile.required_types],
        "selection_rule": dict(profile.selection_rule),
        "target_type": _enum_value(profile.target_type),
    }


def _enum_value(value: object) -> str:
    """枚举或字符串统一取字符串值；只用于审计与事件负载。"""
    return value.value if hasattr(value, "value") else str(value)


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


CHECKSUM_ALGORITHM = "RFC8785_JCS"


def _compute_checksum(record: CalibrationRecord) -> str:
    """按 RFC 8785 JCS 对固定字段计算 checksum（G-15）。"""
    payload = {name: _json_value(getattr(record, name)) for name in _CHECKSUM_FIELDS}
    try:
        return sha256_text(canonical_json(payload))
    except CanonicalJSONError as exc:
        raise DomainValidationError(f"标定字段无法规范化：{exc}") from exc


def _assert_checksum_matches(record: CalibrationRecord) -> None:
    """状态命令前的篡改检测：写库后被人改过就必须拒绝推进。

    这比"只在 DRAFT 更新时重算"更强——后者无法发现绕过用例层的直写。
    """
    expected = _compute_checksum(record)
    if record.checksum != expected:
        # 契约 §8.6：这是专用码，不是 validation_failed——调用方据此知道
        # "记录被改过"，而不是"请求写错了"。
        raise CalibrationChecksumMismatchError(
            "标定 checksum 与内容不一致，记录可能在提交后被篡改",
            details={"calibration_id": str(record.calibration_id), "field": "checksum"},
        )


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
