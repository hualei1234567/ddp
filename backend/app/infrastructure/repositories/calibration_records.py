"""标定配置与标定记录的 SQLAlchemy 仓储。

安全与状态机约束：
- 所有读写由服务端 :class:`ProjectScope` 强制项目隔离；
- ``target_id`` 必须按 ``target_type`` 在项目作用域内存在（SYSTEM 以项目 ID 为目标）；
- ``add`` 只接受 DRAFT，``save`` 只编辑草稿字段且从不写 ``status``；
- 状态迁移只通过具名命令完成，命令只更新状态与校验审计字段；
- 激活使用 SAVEPOINT，先校验状态/版本/有效期，再替换旧 ACTIVE 并写 ``supersedes_id``。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.identity.ports import ProjectMembershipRepository
from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationStatus,
    CalibrationTargetType,
)
from app.domain.devices.errors import (
    CalibrationActiveConflictError,
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectAction, ProjectScope, role_allows
from app.infrastructure.db.models.devices import (
    CalibrationProfile as CalibrationProfileModel,
)
from app.infrastructure.db.models.devices import (
    CalibrationRecord as CalibrationRecordModel,
)
from app.infrastructure.db.models.devices import Device as DeviceModel
from app.infrastructure.db.models.devices import Robot as RobotModel
from app.infrastructure.db.models.devices import Sensor as SensorModel
from app.infrastructure.repositories._sql_support import (
    as_utc,
    bounded_limit,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    is_unique_violation,
    raise_update_failure,
    scoped_select,
)
from app.infrastructure.repositories.calibration_target_refs import (
    ensure_system_target_ref,
    upsert_target_ref,
)
from app.infrastructure.repositories.identity import SqlProjectMembershipRepository

# 标定目标的实体表；SYSTEM 没有独立实体表，约定 target_id == project_id。
_TARGET_MODELS: dict[CalibrationTargetType, type[object]] = {
    CalibrationTargetType.ROBOT: RobotModel,
    CalibrationTargetType.DEVICE: DeviceModel,
    CalibrationTargetType.SENSOR: SensorModel,
}

# 草稿阶段仍不可由客户端修改的字段。
_DRAFT_IMMUTABLE_FIELDS: tuple[str, ...] = (
    "performed_by",
    "validated_by",
    "validated_at",
    "supersedes_id",
)

# SUBMITTED 之后冻结的溯源字段；valid_to 是有效期，可在提交后调整。
_TRACE_FIELDS: tuple[str, ...] = (
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
    "evidence_asset_ids",
    "source",
    "checksum",
    "uncertainty",
    "performed_by",
    "validated_by",
    "validated_at",
    "supersedes_id",
)

_EDITABLE_SAVE_STATUSES = frozenset({CalibrationStatus.DRAFT})
_POST_SUBMIT_STATUSES = frozenset(
    {
        CalibrationStatus.SUBMITTED,
        CalibrationStatus.VALIDATED,
        CalibrationStatus.ACTIVE,
    }
)


def _profile_from_row(row: CalibrationProfileModel) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=row.id,
        project_id=row.project_id,
        name=row.name,
        scope=row.scope,
        target_type=row.target_type,
        # ADR-0032（G-03）：行内业务版本列已改名 profile_version。
        profile_version=row.profile_version,
        schema_version=row.schema_version,
        required_types=tuple(row.required_types or ()),
        selection_rule=row.selection_rule or {},
        target_id=row.target_id,
    )


class SqlCalibrationProfileRepository:
    """配置仓储：只允许插入新版本行，不提供 UPDATE（ADR-0020 / G-07）。

    数据库层也有触发器兜底；这里不提供方法是为了让"改配置=插新版本"
    在代码层面也是唯一路径。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        profile: CalibrationProfile,
    ) -> CalibrationProfile:
        scope.ensure(profile.project_id)
        # 注意：CalibrationProfile.profile_version 是配置版本号（DDP-101 §2.2），
        # 不是乐观锁版本，因此允许从任意正整数版本开始。
        row = CalibrationProfileModel(
            id=profile.profile_id,
            target_id=profile.target_id,
            project_id=profile.project_id,
            name=profile.name,
            scope=enum_value(profile.scope),
            target_type=enum_value(profile.target_type),
            profile_version=profile.profile_version,
            schema_version=profile.schema_version,
            required_types=[enum_value(item) for item in profile.required_types],
            selection_rule=dict(profile.selection_rule),
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return profile

    def get(
        self,
        scope: ProjectScope,
        profile_id: UUID,
    ) -> CalibrationProfile | None:
        row = self._session.scalar(
            select(CalibrationProfileModel).where(
                CalibrationProfileModel.id == profile_id,
                CalibrationProfileModel.project_id == scope.project_id,
            )
        )
        return _profile_from_row(row) if row is not None else None


def _ensure_target_in_project(
    session: Session,
    scope: ProjectScope,
    target_type: CalibrationTargetType | str,
    target_id: UUID,
) -> None:
    """按 target_type 校验目标存在且属于当前项目；跨项目一律按不存在处理。

    校验通过后同事务维护 ``calibration_target_refs`` 投影行，供
    ``calibration_records`` 的复合外键/触发器做数据库级兜底。
    """
    try:
        resolved = CalibrationTargetType(target_type)
    except ValueError as exc:
        raise DomainValidationError("target_type 取值非法") from exc
    if resolved is CalibrationTargetType.SYSTEM:
        if target_id != scope.project_id:
            raise DomainValidationError("SYSTEM 标定的 target_id 必须等于当前项目 ID")
        ensure_system_target_ref(session, scope.project_id)
        return
    model = _TARGET_MODELS[resolved]
    found = session.execute(
        select(model.id, model.status).where(  # type: ignore[attr-defined]
            model.id == target_id,  # type: ignore[attr-defined]
            model.project_id == scope.project_id,  # type: ignore[attr-defined]
        )
    ).first()
    if found is None:
        raise ResourceNotFoundError(
            "标定目标",
            target_id,
            code="calibration_target_invalid",
        )
    upsert_target_ref(
        session,
        project_id=scope.project_id,
        target_type=resolved,
        target_id=target_id,
        lifecycle_status=found.status,
    )


def _ensure_profile_in_project(
    session: Session,
    scope: ProjectScope,
    profile_id: UUID,
    target_type: CalibrationTargetType | str | None = None,
) -> None:
    row = session.scalar(
        select(
            CalibrationProfileModel.target_type,
        ).where(
            CalibrationProfileModel.id == profile_id,
            CalibrationProfileModel.project_id == scope.project_id,
        )
    )
    if row is None:
        raise ResourceNotFoundError(
            "标定配置",
            profile_id,
            code="calibration_profile_invalid",
        )
    if target_type is not None and row != enum_value(target_type):
        raise DomainValidationError("标定配置 target_type 与记录不一致")


def _ensure_performer_is_actor(scope: ProjectScope, performed_by: UUID) -> None:
    """新记录的执行人必须就是当前认证主体，禁止代填他人。"""
    if performed_by != scope.actor_id:
        raise DomainValidationError("performed_by 必须等于当前主体")


def _ensure_performer_unchanged(persisted: UUID, submitted: UUID) -> None:
    if submitted != persisted:
        raise ResourceImmutableError("performed_by 不可修改")


def _assert_fields_unchanged(
    current: CalibrationRecord,
    submitted: CalibrationRecord,
    fields: tuple[str, ...],
    message: str,
) -> None:
    changed = sorted(name for name in fields if getattr(current, name) != getattr(submitted, name))
    if changed:
        raise ResourceImmutableError(f"{message}：{', '.join(changed)}")


def _record_insert_values(record: CalibrationRecord, *, actor_id: UUID) -> dict[str, object]:
    """新增 DRAFT 快照；调用方已保证 status=DRAFT 且无校验/替代字段。"""
    return {
        "profile_id": record.profile_id,
        "scope": enum_value(record.scope),
        "target_type": enum_value(record.target_type),
        "target_id": record.target_id,
        "calibration_type": enum_value(record.calibration_type),
        "method": record.method,
        "parameters": dict(record.parameters),
        "parent_frame": record.parent_frame,
        "child_frame": record.child_frame,
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
        "status": enum_value(record.status),
        "evidence_asset_ids": [str(item) for item in record.evidence_asset_ids],
        "source": record.source,
        "checksum": record.checksum,
        "uncertainty": dict(record.uncertainty),
        "performed_by": record.performed_by,
        "validated_by": record.validated_by,
        "validated_at": record.validated_at,
        "supersedes_id": record.supersedes_id,
        # ADR-0020：业务修订号与 Profile 快照在创建时写入。
        "calibration_version": record.calibration_version,
        "profile_version": record.profile_version,
        "profile_schema_version": record.profile_schema_version,
        "profile_snapshot": dict(record.profile_snapshot),
        "updated_by": actor_id,
    }


def _draft_editable_values(record: CalibrationRecord, *, actor_id: UUID) -> dict[str, object]:
    """DRAFT 的编辑白名单；绝不包含 status、校验审计与 supersedes_id。

    ``calibration_version`` 与 Profile 快照三字段**必须**在这里：缺了它们，
    ``update_calibration`` 无论把业务修订号算成多少，落库都写不进去，
    版本号会永远停在 1（G-04），换 profile 后快照也会与 ``profile_id`` 脱节（G-05）。
    """
    return {
        "profile_id": record.profile_id,
        "scope": enum_value(record.scope),
        "target_type": enum_value(record.target_type),
        "target_id": record.target_id,
        "calibration_type": enum_value(record.calibration_type),
        "method": record.method,
        "parameters": dict(record.parameters),
        "parent_frame": record.parent_frame,
        "child_frame": record.child_frame,
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
        "evidence_asset_ids": [str(item) for item in record.evidence_asset_ids],
        "source": record.source,
        "checksum": record.checksum,
        "uncertainty": dict(record.uncertainty),
        "calibration_version": record.calibration_version,
        "profile_version": record.profile_version,
        "profile_schema_version": record.profile_schema_version,
        "profile_snapshot": dict(record.profile_snapshot),
        "updated_by": actor_id,
    }


def _record_from_row(row: CalibrationRecordModel) -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=row.id,
        project_id=row.project_id,
        profile_id=row.profile_id,
        scope=row.scope,
        target_type=row.target_type,
        target_id=row.target_id,
        calibration_type=row.calibration_type,
        method=row.method,
        parameters=row.parameters or {},
        parent_frame=row.parent_frame,
        child_frame=row.child_frame,
        valid_from=_as_utc_required(row.valid_from),
        valid_to=as_utc(row.valid_to),
        status=CalibrationStatus(row.status),
        evidence_asset_ids=tuple(UUID(item) for item in (row.evidence_asset_ids or [])),
        source=row.source,
        checksum=row.checksum,
        uncertainty=row.uncertainty or {},
        performed_by=row.performed_by,
        validated_by=row.validated_by,
        validated_at=as_utc(row.validated_at),
        supersedes_id=row.supersedes_id,
        version=row.version,
        calibration_version=row.calibration_version,
        profile_version=row.profile_version,
        profile_schema_version=row.profile_schema_version,
        profile_snapshot=row.profile_snapshot or {},
    )


def _as_utc_required(value: datetime) -> datetime:
    converted = as_utc(value)
    if converted is None:
        raise DomainValidationError("valid_from 不能为空")
    return converted


class SqlCalibrationRecordRepository:
    def __init__(
        self,
        session: Session,
        memberships: ProjectMembershipRepository | None = None,
    ) -> None:
        self._session = session
        self._memberships = memberships or SqlProjectMembershipRepository(session)

    def _ensure_validator_eligible(
        self,
        scope: ProjectScope,
        validated_by: UUID | None,
    ) -> None:
        """校验人必须是本项目成员，且角色拥有 VALIDATE 权限。"""
        if validated_by is None:
            return
        role = self._memberships.role_for(scope.project_id, validated_by)
        if not role_allows(role, ProjectAction.VALIDATE):
            raise DomainValidationError("validated_by 必须是本项目有校验权限的成员")

    def _get_owned_row(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
    ) -> CalibrationRecordModel:
        row = self._session.scalar(
            select(CalibrationRecordModel).where(
                CalibrationRecordModel.id == calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
            )
        )
        if row is None:
            raise ResourceNotFoundError("标定", calibration_id)
        return row

    def _load_for_command(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        expected_version: int,
    ) -> tuple[CalibrationRecordModel, CalibrationRecord]:
        """命令入口：以持久化行为权威，拒绝陈旧版本或伪造状态。"""
        scope.ensure(record.project_id)
        ensure_expected_version(record.version, expected_version)
        row = self._get_owned_row(scope, record.calibration_id)
        if row.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        if row.status != enum_value(record.status):
            raise DomainValidationError("传入记录状态与持久化状态不一致，请重新读取")
        return row, _record_from_row(row)

    def add(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
    ) -> CalibrationRecord:
        scope.ensure(record.project_id)
        ensure_initial_version(record.version)
        _ensure_performer_is_actor(scope, record.performed_by)
        if record.status is not CalibrationStatus.DRAFT:
            raise DomainValidationError("新增标定记录必须为 DRAFT 状态")
        if record.validated_by is not None or record.validated_at is not None:
            raise DomainValidationError("DRAFT 标定记录不允许携带校验人字段")
        if record.supersedes_id is not None:
            raise DomainValidationError("新增标定记录不允许设置 supersedes_id")
        _ensure_target_in_project(
            self._session,
            scope,
            record.target_type,
            record.target_id,
        )
        _ensure_profile_in_project(
            self._session,
            scope,
            record.profile_id,
            record.target_type,
        )
        row = CalibrationRecordModel(
            id=record.calibration_id,
            project_id=record.project_id,
            created_by=scope.actor_id,
            **_record_insert_values(record, actor_id=scope.actor_id),
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "项目内标定记录冲突")
        return record

    def get(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
    ) -> CalibrationRecord | None:
        row = self._session.scalar(
            select(CalibrationRecordModel).where(
                CalibrationRecordModel.id == calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
            )
        )
        return _record_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        target_type: str | None = None,
        target_id: UUID | None = None,
        status: CalibrationStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CalibrationRecord]:
        statement = scoped_select(CalibrationRecordModel, scope)
        if target_type is not None:
            statement = statement.where(CalibrationRecordModel.target_type == target_type)
        if target_id is not None:
            statement = statement.where(CalibrationRecordModel.target_id == target_id)
        if status is not None:
            statement = statement.where(CalibrationRecordModel.status == status.value)
        if after_id is not None:
            statement = statement.where(CalibrationRecordModel.id > after_id)
        statement = statement.order_by(CalibrationRecordModel.id).limit(
            bounded_limit(limit, maximum=101)
        )
        return [_record_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        """编辑 DRAFT；不写 status；SUBMITTED/VALIDATED/ACTIVE 仅允许调整 valid_to。"""
        scope.ensure(record.project_id)
        ensure_expected_version(record.version, expected_version)
        row = self._get_owned_row(scope, record.calibration_id)
        if row.version != expected_version:
            raise VersionConflictError("标定", expected_version)
        current = _record_from_row(row)
        if record.status is not current.status:
            raise ResourceImmutableError("save 不允许修改标定状态，请使用状态变更命令")
        _ensure_performer_unchanged(current.performed_by, record.performed_by)

        if current.status in _EDITABLE_SAVE_STATUSES:
            _assert_fields_unchanged(
                current,
                record,
                _DRAFT_IMMUTABLE_FIELDS,
                "草稿修改包含不可变字段",
            )
            _ensure_target_in_project(
                self._session,
                scope,
                record.target_type,
                record.target_id,
            )
            _ensure_profile_in_project(
                self._session,
                scope,
                record.profile_id,
                record.target_type,
            )
            values = _draft_editable_values(record, actor_id=scope.actor_id)
        elif current.status in _POST_SUBMIT_STATUSES:
            _assert_fields_unchanged(
                current,
                record,
                _TRACE_FIELDS,
                "已提交标定不允许修改溯源字段",
            )
            values = {"valid_to": record.valid_to, "updated_by": scope.actor_id}
        else:
            raise ResourceImmutableError(f"{current.status.value} 状态标定不可修改")

        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == record.calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.version == expected_version,
                CalibrationRecordModel.status == current.status.value,
            )
            .values(**values, version=expected_version + 1)
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CalibrationRecordModel,
                scope,
                record.calibration_id,
                "标定",
                expected_version,
                expected_status=current.status.value,
            )
        return replace(record, version=expected_version + 1)

    def _apply_transition(
        self,
        scope: ProjectScope,
        current: CalibrationRecord,
        target: CalibrationRecord,
        expected_version: int,
    ) -> CalibrationRecord:
        """命令型状态迁移：只更新 status、校验审计、updated_by 与版本。"""
        values: dict[str, object] = {
            "status": target.status.value,
            "version": expected_version + 1,
            "updated_by": scope.actor_id,
        }
        if target.validated_by != current.validated_by:
            values["validated_by"] = target.validated_by
        if target.validated_at != current.validated_at:
            values["validated_at"] = target.validated_at
        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == current.calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.version == expected_version,
                CalibrationRecordModel.status == current.status.value,
            )
            .values(**values)
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CalibrationRecordModel,
                scope,
                current.calibration_id,
                "标定",
                expected_version,
                expected_status=current.status.value,
            )
        return replace(target, version=expected_version + 1)

    def submit(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        # DDP-101 §3.2：提交前目标必须仍存在于当前项目。
        _ensure_target_in_project(
            self._session,
            scope,
            current.target_type,
            current.target_id,
        )
        _ensure_profile_in_project(
            self._session,
            scope,
            current.profile_id,
            current.target_type,
        )
        return self._apply_transition(scope, current, current.submit(), expected_version)

    def validate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        validator_id: UUID,
        validated_at: datetime,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        self._ensure_validator_eligible(scope, validator_id)
        validated = current.validate(validator_id, validated_at)
        return self._apply_transition(scope, current, validated, expected_version)

    def reject(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        return self._apply_transition(scope, current, current.reject(), expected_version)

    def reopen(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        return self._apply_transition(scope, current, current.reopen(), expected_version)

    def revoke(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        return self._apply_transition(scope, current, current.revoke(), expected_version)

    def expire(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        _, current = self._load_for_command(scope, record, expected_version)
        return self._apply_transition(scope, current, current.expire(), expected_version)

    def _find_active_id(
        self,
        scope: ProjectScope,
        current: CalibrationRecord,
    ) -> UUID | None:
        return self._session.scalar(
            select(CalibrationRecordModel.id)
            .where(
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.scope == enum_value(current.scope),
                CalibrationRecordModel.target_type == enum_value(current.target_type),
                CalibrationRecordModel.target_id == current.target_id,
                CalibrationRecordModel.calibration_type == enum_value(current.calibration_type),
                CalibrationRecordModel.status == CalibrationStatus.ACTIVE.value,
                CalibrationRecordModel.id != current.calibration_id,
            )
            .limit(1)
        )

    def _supersede_active(
        self,
        scope: ProjectScope,
        old_active_id: UUID,
        expected_version: int,
    ) -> None:
        """把同目标旧 ACTIVE 原子转为 SUPERSEDED；不重写其溯源字段。"""
        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == old_active_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.status == CalibrationStatus.ACTIVE.value,
            )
            .values(
                status=CalibrationStatus.SUPERSEDED.value,
                version=CalibrationRecordModel.version + 1,
                updated_by=scope.actor_id,
            )
        )
        if result.rowcount != 1:
            # 并发事务已改变旧 ACTIVE；回滚整个 savepoint，避免部分写入。
            raise VersionConflictError("标定", expected_version)

    def _activate_current(
        self,
        scope: ProjectScope,
        activated: CalibrationRecord,
        old_active_id: UUID | None,
        expected_version: int,
    ) -> None:
        values: dict[str, object] = {
            "status": CalibrationStatus.ACTIVE.value,
            "version": expected_version + 1,
            "updated_by": scope.actor_id,
        }
        if old_active_id is not None:
            values["supersedes_id"] = old_active_id
        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == activated.calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.version == expected_version,
                CalibrationRecordModel.status == CalibrationStatus.VALIDATED.value,
            )
            .values(**values)
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CalibrationRecordModel,
                scope,
                activated.calibration_id,
                "标定",
                expected_version,
                expected_status=CalibrationStatus.VALIDATED.value,
            )

    def activate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> CalibrationRecord:
        """VALIDATED→ACTIVE；先校验，再在 savepoint 内替换旧 ACTIVE。"""
        _, current = self._load_for_command(scope, record, expected_version)
        if current.status is not CalibrationStatus.VALIDATED:
            raise InvalidStateTransitionError(
                "标定",
                current.status.value,
                CalibrationStatus.ACTIVE.value,
            )
        _ensure_target_in_project(
            self._session,
            scope,
            current.target_type,
            current.target_id,
        )
        activated = current.activate(now=now)

        old_active_id: UUID | None = None
        try:
            with self._session.begin_nested():
                old_active_id = self._find_active_id(scope, current)
                if old_active_id is not None:
                    self._supersede_active(scope, old_active_id, expected_version)
                self._activate_current(scope, activated, old_active_id, expected_version)
        except IntegrityError as exc:
            # 契约 §8.6：同目标已有 ACTIVE **且无法原子替代** 才是
            # ``calibration_active_conflict``。能替代的情形在上面 savepoint 内
            # 正常完成，走不到这里；到这里说明并发让我们没能把旧 ACTIVE
            # 转成 SUPERSEDED，被部分唯一索引挡住——这是冲突，不是业务键重复。
            if is_unique_violation(exc):
                raise CalibrationActiveConflictError(
                    "同一标定目标已有 ACTIVE 记录，且无法原子替代",
                    details={
                        "target_type": enum_value(current.target_type),
                        "calibration_type": enum_value(current.calibration_type),
                    },
                ) from exc
            raise

        return replace(
            activated,
            version=expected_version + 1,
            supersedes_id=old_active_id or current.supersedes_id,
        )
