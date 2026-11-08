"""DatasetGrant 领域实体：跨项目/跨团队数据集显式授权。

默认 PENDING_APPROVAL，PO 审批后 ACTIVE；必须带范围、到期时间与原因，
可撤销。到期与撤销立即生效：读取时按当前时间派生 EXPIRED，不依赖后台任务。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.training_enums import DatasetGrantStatus, GrantScope
from app.domain.datasets.training_errors import (
    DatasetGrantExpiredError,
    DatasetGrantForbiddenError,
    DatasetGrantRevokedError,
)
from app.domain.datasets.training_state_machines import (
    allowed_grant_actions,
    grant_target,
)
from app.domain.datasets.validation import (
    aware_datetime,
    coerce_enum,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    text_value,
)


def _scope(value: object) -> list[str]:
    if not isinstance(value, list | tuple) or not value:
        raise DomainValidationError("scope 必须是非空数组")
    result: list[str] = []
    for item in value:
        scope = coerce_enum(item, GrantScope, "scope")
        if scope.value not in result:
            result.append(scope.value)
    return result


@dataclass(frozen=True, slots=True)
class DatasetGrant:
    grant_id: UUID
    project_id: UUID
    dataset_id: UUID
    grantee_project_id: UUID
    scope: list[str]
    reason: str
    expires_at: datetime
    status: DatasetGrantStatus | str = DatasetGrantStatus.PENDING_APPROVAL
    dataset_version_id: UUID | None = None
    grantee_team: str | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None
    revoke_reason: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("grant_id", "project_id", "dataset_id", "grantee_project_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self, "dataset_version_id", optional_uuid(self.dataset_version_id, "dataset_version_id")
        )
        object.__setattr__(self, "scope", _scope(self.scope))
        object.__setattr__(self, "reason", text_value(self.reason, "reason", max_length=2000))
        object.__setattr__(
            self, "grantee_team", optional_text(self.grantee_team, "grantee_team", max_length=200)
        )
        object.__setattr__(self, "expires_at", aware_datetime(self.expires_at, "expires_at"))
        object.__setattr__(self, "status", coerce_enum(self.status, DatasetGrantStatus, "status"))
        for name in ("approved_by", "rejected_by", "revoked_by", "created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        for name in (
            "approved_at",
            "rejected_at",
            "revoked_at",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("rejection_reason", "revoke_reason"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=2000),
            )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        status = DatasetGrantStatus(self.status)
        if status is DatasetGrantStatus.ACTIVE:
            if self.approved_by is None or self.approved_at is None:
                raise DomainValidationError("ACTIVE 授权必须记录审批人/时间")
        if status is DatasetGrantStatus.REJECTED and self.rejection_reason is None:
            raise DomainValidationError("REJECTED 授权必须记录驳回原因")
        if status is DatasetGrantStatus.REVOKED:
            if self.revoked_by is None or self.revoked_at is None or self.revoke_reason is None:
                raise DomainValidationError("REVOKED 授权必须记录撤销人/时间/原因")

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_grant_actions(DatasetGrantStatus(self.status))

    def status_at(self, now: datetime) -> DatasetGrantStatus:
        """到期立即失效：ACTIVE 且到达 expires_at 视为 EXPIRED。"""
        status = DatasetGrantStatus(self.status)
        if status is DatasetGrantStatus.ACTIVE and now >= self.expires_at:
            return DatasetGrantStatus.EXPIRED
        return status

    def allows(self, required: GrantScope, now: datetime) -> bool:
        status = self.status_at(now)
        if status is DatasetGrantStatus.EXPIRED:
            raise DatasetGrantExpiredError("数据集授权已过期，立即失效")
        if status is DatasetGrantStatus.REVOKED:
            raise DatasetGrantRevokedError("数据集授权已撤销，立即失效")
        if status is not DatasetGrantStatus.ACTIVE:
            return False
        return required.value in self.scope

    def approve(self, *, actor_id: UUID, now: datetime) -> DatasetGrant:
        if self.created_by is not None and actor_id == self.created_by:
            raise DatasetGrantForbiddenError("授权创建人不得审批自己的授权")
        if now >= self.expires_at:
            raise DatasetGrantExpiredError("授权已过期，不能审批")
        target = grant_target(DatasetGrantStatus(self.status), "approve")
        return replace(
            self,
            status=target,
            approved_by=actor_id,
            approved_at=now,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def reject(self, *, actor_id: UUID, now: datetime, reason: str) -> DatasetGrant:
        if self.created_by is not None and actor_id == self.created_by:
            raise DatasetGrantForbiddenError("授权创建人不得驳回自己的授权")
        target = grant_target(DatasetGrantStatus(self.status), "reject")
        return replace(
            self,
            status=target,
            rejected_by=actor_id,
            rejected_at=now,
            rejection_reason=text_value(reason, "rejection_reason", max_length=2000),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def revoke(self, *, actor_id: UUID, now: datetime, reason: str) -> DatasetGrant:
        target = grant_target(DatasetGrantStatus(self.status), "revoke")
        return replace(
            self,
            status=target,
            revoked_by=actor_id,
            revoked_at=now,
            revoke_reason=text_value(reason, "revoke_reason", max_length=2000),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def expire(self, *, now: datetime) -> DatasetGrant:
        target = grant_target(DatasetGrantStatus(self.status), "system-expire")
        return replace(
            self,
            status=target,
            updated_at=now,
            row_version=self.row_version + 1,
        )


def new_dataset_grant(
    *,
    grant_id: UUID,
    project_id: UUID,
    dataset_id: UUID,
    dataset_version_id: UUID | None,
    grantee_project_id: UUID,
    grantee_team: str | None,
    scope: object,
    reason: str,
    expires_at: datetime,
    actor_id: UUID,
    now: datetime,
) -> DatasetGrant:
    if scope is None:
        raise DomainValidationError("scope 必须是非空数组")
    return DatasetGrant(
        grant_id=grant_id,
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        grantee_project_id=grantee_project_id,
        grantee_team=grantee_team,
        scope=_scope(scope),
        reason=reason,
        expires_at=expires_at,
        status=DatasetGrantStatus.PENDING_APPROVAL,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


__all__ = ["DatasetGrant", "new_dataset_grant"]
