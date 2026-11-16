"""采集素材写路径用例：PATCH / 状态动作 / DELETE（N8）。

为什么是协作 mixin 而不是塞进 ``assets.py``
-------------------------------------------
``assets.py`` 已承载四类素材的"创建 / 列表 / 详情"与依赖装配，再塞进写路径
会让该文件越过 500 行的评审阈值。按"读路径 / 写路径"拆开是沿职责切分，
不是按文件大小随便切：读路径没有事务、审计、幂等；写路径三样都要，
且三者的顺序（预检 → 落库 → 留痕 → 幂等 → 提交）是必须一致的不变量。

本模块只声明自己**使用**的依赖属性（由 ``_AssetUseCases.__init__`` 注入），
不定义 ``__init__``——mixin 与基类共享同一份依赖装配，避免两个构造器。

三条写路径的一致性要求
----------------------
- **状态动作**：幂等键 + ``If-Match`` 乐观锁，与任务/批次一致；
- **PATCH**：``If-Match`` 必填、不记幂等键（与 ``tasks.update_task`` 一致——
  ``If-Match`` 已让重复提交变成 409，再加幂等键只会把真实版本冲突伪装成重放）；
- **DELETE**：不记幂等键，重复删除返回 404（HTTP 语义允许，且能让调用方
  知道"这行已经不在"）。

每次写都同事务写审计与 outbox（``record_change``），事件名走模块级常量，
见 ``assets.py`` —— ``scripts/check_outbox_events.py`` 只认字面量与常量。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Generic, TypeVar
from uuid import UUID

from app.application.collection.audit import record_change
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.ports import (
    AuditRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.collection.asset_lifecycle import (
    apply_status_action,
    apply_update,
    assert_delete_allowed,
    assert_patch_allowed,
)
from app.domain.collection.assets import (
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.enums import PUBLIC_ASSET_ACTIONS, CollectionAssetStatus
from app.domain.collection.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.collection.validation import coerce_enum
from app.domain.identity.models import ProjectScope

if TYPE_CHECKING:  # 避免与 ``assets.py`` 形成导入环：那边在模块顶层导入本 mixin。
    from app.application.collection.assets import AssetSpec

AssetT = TypeVar("AssetT", CollectionPlan, CollectionSop, Scenario, ActionSpec)


class AssetMutationMixin(Generic[AssetT]):
    """素材写路径：``update`` / ``apply_action`` / ``delete``。

    依赖属性由 ``_AssetUseCases`` 注入；这里只声明类型，不定义 ``__init__``。
    """

    spec: AssetSpec[AssetT]
    _repository: object
    _audit: AuditRepository
    _outbox: OutboxRepository
    _idempotency: IdempotencyGuard
    _uow: UnitOfWork

    # 由基类提供；此处声明只为让 mixin 自身类型可读。
    def get(self, scope: ProjectScope, asset_id: UUID) -> AssetT:  # pragma: no cover
        raise NotImplementedError

    def _id_of(self, asset: AssetT) -> UUID:  # pragma: no cover
        raise NotImplementedError

    # --- PATCH ---------------------------------------------------------------
    def update(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> AssetT:
        """按字段白名单更新素材；``code`` 与执行内容字段不可改。

        字段白名单与状态闸门都在领域层（``asset_lifecycle.assert_patch_allowed``），
        这里只做编排。``If-Match`` 与仓储的 ``WHERE version = ?`` 共同保证并发安全。
        """
        current = self.get(scope, asset_id)
        if current.version != expected_version:
            raise VersionConflictError(self.spec.resource_name)
        assert_patch_allowed(
            _status_of(current),
            changes,
            patchable=self.spec.patchable,
            resource_name=self.spec.resource_name,
        )

        now = _utcnow()
        candidate = apply_update(current, changes, now=now, actor_id=scope.actor_id)
        saved = self._save(scope, candidate, expected_version)
        fields = sorted(changes)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=self.spec.resource_type,
            resource_id=self._id_of(saved),
            action="update",
            from_status=_status_of(current).value,
            to_status=_status_of(saved).value,
            reason=None,
            request_id=request_id,
            details={"fields": fields},
            event_type=self.spec.updated_event,
            event_payload={
                self.spec.id_field: str(self._id_of(saved)),
                "code": saved.code,
                "fields": fields,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    # --- 状态动作 -------------------------------------------------------------
    def apply_action(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AssetT:
        """``activate`` / ``archive``；``ARCHIVED`` 是终态，不可迁出。"""
        key = validate_key(idempotency_key)
        reason = _reason(payload)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get(scope, _decoded_ref(replay.response_ref, self.spec.resource_name))

        if action not in PUBLIC_ASSET_ACTIONS:
            raise DomainValidationError("未知状态动作")
        unknown = set(payload or {}) - {"reason"}
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")

        current = self.get(scope, asset_id)
        if current.version != expected_version:
            raise VersionConflictError(self.spec.resource_name)

        # 状态机在领域层：非法迁移与终态回退在这里显式失败，不存在默认成功分支。
        candidate, from_status, to_status = apply_status_action(
            current,
            action,
            now=now,
            actor_id=scope.actor_id,
        )
        saved = self._save(scope, candidate, expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=self.spec.resource_type,
            resource_id=self._id_of(saved),
            action=action,
            from_status=from_status.value,
            to_status=to_status.value,
            reason=reason,
            request_id=request_id,
            details={"code": saved.code},
            event_type=self.spec.status_event,
            event_payload={
                self.spec.id_field: str(self._id_of(saved)),
                "code": saved.code,
                "action": action,
                "from_status": from_status.value,
                "to_status": to_status.value,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(self._id_of(saved)),
            now=now,
        )
        self._uow.commit()
        return saved

    # --- DELETE --------------------------------------------------------------
    def delete(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        *,
        request_id: str | None = None,
    ) -> None:
        """硬删除 ``DRAFT`` 且未被任何任务引用的素材。

        硬删而不是软删：未被任务引用时（``ON DELETE RESTRICT`` 保证）没有任何
        外部事实依赖它；``ARCHIVED`` 已承担"保留但退场"的语义，再加 ``deleted_at``
        会让"这个 ID 还在不在"出现第三种答案。删除动作本身写审计，
        "删过什么"仍可追溯。
        """
        current = self.get(scope, asset_id)
        assert_delete_allowed(_status_of(current), resource_name=self.spec.resource_name)
        repository = self._repository
        repository.delete_draft(scope, asset_id)  # type: ignore[attr-defined]
        now = _utcnow()
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=self.spec.resource_type,
            resource_id=asset_id,
            action="delete",
            from_status=_status_of(current).value,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={"code": current.code},
            event_type=self.spec.deleted_event,
            event_payload={
                self.spec.id_field: str(asset_id),
                "code": current.code,
                "status": _status_of(current).value,
            },
            occurred_at=now,
        )
        self._uow.commit()

    # --- 内部 ---------------------------------------------------------------
    def _save(
        self,
        scope: ProjectScope,
        candidate: AssetT,
        expected_version: int,
    ) -> AssetT:
        repository = self._repository
        return repository.save_update(  # type: ignore[attr-defined,no-any-return]
            scope,
            candidate,
            expected_version=expected_version,
        )


def _status_of(asset: AssetT) -> CollectionAssetStatus:
    """把实体上的状态归一成枚举；未知取值抛领域错误而不是 ``ValueError``。

    用 ``coerce_enum`` 而不是 ``CollectionAssetStatus(status)``：后者会抛裸
    ``ValueError``，绕过域错误映射变成 500；未知状态必须显式失败且可映射。
    """
    return coerce_enum(asset.status, CollectionAssetStatus, "status")


def _decoded_ref(response_ref: str | None, resource_name: str) -> UUID:
    """幂等重放记录里存的是资源 ID 字符串；非法值按 404 处理，不泄漏内部格式。"""
    if not response_ref:
        raise ResourceNotFoundError(resource_name, response_ref)
    try:
        return UUID(response_ref)
    except ValueError as exc:
        raise ResourceNotFoundError(resource_name, response_ref) from exc


def _reason(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("reason")
    return value if isinstance(value, str) else None


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["AssetMutationMixin"]
