"""标定配置用例：登记新版本、按 ID 查询。

Profile 是**不可变配置版本**（ADR-0020 / G-07）：改配置 = 插入新版本行，
仓储不提供 UPDATE，数据库触发器也拦 UPDATE/DELETE。因此本模块只有
"登记"与"查询"两类操作，没有 update。

复用说明（CLAUDE.md §一）：``CalibrationProfile`` 与
``SqlCalibrationProfileRepository`` 早已存在并被标定用例使用（G-04/05/06）；
此前缺的是对外登记入口（只能由测试直接插库），G-01 补上。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import record_change
from app.application.devices.commands import NewCalibrationProfile
from app.application.devices.idempotency import IdempotencyGuard, validate_key
from app.application.devices.ports import (
    AuditRepository,
    CalibrationProfileRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.common.ids import new_id
from app.domain.devices.calibration import CalibrationProfile
from app.domain.devices.enums import CalibrationScope, CalibrationTargetType
from app.domain.devices.errors import DomainValidationError, ResourceNotFoundError
from app.domain.identity.models import ProjectScope


class CalibrationProfileUseCases:
    def __init__(
        self,
        profiles: CalibrationProfileRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._profiles = profiles
        self._uow = uow
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)

    def get_profile(self, scope: ProjectScope, profile_id: UUID) -> CalibrationProfile:
        profile = self._profiles.get(scope, profile_id)
        if profile is None:
            raise ResourceNotFoundError("标定配置", profile_id)
        return profile

    def create_profile(
        self,
        scope: ProjectScope,
        command: NewCalibrationProfile,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CalibrationProfile:
        key = validate_key(idempotency_key)
        body = {
            "name": command.name,
            "scope": command.scope,
            "target_type": command.target_type,
            "schema_version": command.schema_version,
            "required_types": list(command.required_types),
            "target_id": str(command.target_id) if command.target_id else None,
            "profile_version": command.profile_version,
            "supersedes_id": str(command.supersedes_id) if command.supersedes_id else None,
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        _ensure_target_type_matches_scope(command.scope, command.target_type)
        if command.supersedes_id is not None:
            self._validate_supersedes(scope, command)

        profile = CalibrationProfile(
            profile_id=new_id(),
            project_id=scope.project_id,
            name=command.name,
            scope=command.scope,
            target_type=command.target_type,
            profile_version=command.profile_version,
            schema_version=command.schema_version,
            required_types=command.required_types,
            selection_rule=command.selection_rule,
            target_id=command.target_id,
        )
        created = self._profiles.add(scope, profile)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="calibration_profile",
            resource_id=created.profile_id,
            action="create",
            from_status=None,
            to_status=str(created.scope),
            reason=None,
            request_id=request_id,
            details={
                "name": created.name,
                "profile_version": created.profile_version,
                "target_type": str(created.target_type),
                "target_id": str(created.target_id) if created.target_id else None,
            },
            event_type="calibration_profile.created",
            event_payload={
                "profile_id": str(created.profile_id),
                "name": created.name,
                "profile_version": created.profile_version,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.profile_id),
            now=now,
        )
        self._uow.commit()
        return created

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> CalibrationProfile:
        if not response_ref:
            raise ResourceNotFoundError("标定配置", response_ref)
        try:
            profile_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标定配置", response_ref) from exc
        return self.get_profile(scope, profile_id)

    def _validate_supersedes(
        self,
        scope: ProjectScope,
        command: NewCalibrationProfile,
    ) -> None:
        """新版本必须指向同项目、同作用域/目标类型的上一版，且版本号连续。"""
        assert command.supersedes_id is not None
        previous = self._profiles.get(scope, command.supersedes_id)
        if previous is None:
            raise DomainValidationError("supersedes_id 指向的标定配置不存在或不属于当前项目")
        same_target = (
            previous.scope == CalibrationScope(command.scope)
            and previous.target_type == CalibrationTargetType(command.target_type)
            and previous.target_id == command.target_id
        )
        if not same_target:
            raise DomainValidationError("supersedes_id 必须指向同作用域、同目标类型的上一版配置")
        expected = previous.profile_version + 1
        if command.profile_version != expected:
            raise DomainValidationError(f"profile_version 必须为上一版 +1（期望 {expected}）")


def _ensure_target_type_matches_scope(scope: str, target_type: str) -> None:
    """冻结契约要求 target_type 等于 scope；防止把 scope 常量误填进 target_type。"""
    try:
        scope_value = CalibrationScope(scope)
        target_value = CalibrationTargetType(target_type)
    except ValueError as exc:
        raise DomainValidationError("scope / target_type 取值非法") from exc
    if scope_value.value != target_value.value:
        raise DomainValidationError("target_type 必须等于 scope")


__all__ = ["CalibrationProfileUseCases"]
