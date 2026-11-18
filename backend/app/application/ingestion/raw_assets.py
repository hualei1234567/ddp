"""RawAsset 用例：查询、筛选与状态动作。

状态动作（隔离放行、驳回隔离、事后隔离、归档、墓碑）此前只在领域状态机里
有定义，没有任何 HTTP 入口：被隔离的资产永远放不出来，资产也无法归档或
墓碑，界面上"隔离原因""墓碑时间"这些状态用户永远造不出来。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.application.collection.audit import record_change
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import (
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.ingestion.ports import RawAssetRepository
from app.application.ingestion.raw_asset_validation import VALIDATION_EVENT_TYPE
from app.application.ingestion.upload_audit import make_outbox_event
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import PUBLIC_RAW_ASSET_ACTIONS, RawAssetStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.ingestion.models import RawAsset

# 动作可提交字段：原因与隔离码。多传字段一律拒绝，避免客户端把
# "改状态"当成"改内容"的后门。
_ACTION_FIELDS = frozenset({"reason", "quarantine_codes"})


class RawAssetUseCases:
    def __init__(
        self,
        assets: RawAssetRepository,
        *,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        idempotency: IdempotencyRepository | None = None,
        uow: UnitOfWork | None = None,
    ) -> None:
        self._assets = assets
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency) if idempotency is not None else None
        # 幂等重放标记供 API 设置响应头；用例实例按请求装配，不跨请求共享。
        self.last_replayed = False
        self._uow = uow

    def list_assets(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        collection_task_id: UUID | None = None,
        format: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[RawAsset]:
        bounded = normalize_limit(limit)
        items = self._assets.list(
            scope,
            status=_asset_status(status),
            collection_task_id=collection_task_id,
            format=format,
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=lambda item: item.asset_id)

    def get_asset(self, scope: ProjectScope, asset_id: UUID) -> RawAsset:
        asset = self._assets.get(scope, asset_id)
        if asset is None:
            raise ResourceNotFoundError("原始资产", asset_id)
        return asset

    @property
    def _guard(self) -> IdempotencyGuard:
        if self._idempotency is None:
            raise RuntimeError("RawAsset 动作用例缺少幂等依赖")
        return self._idempotency

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
        path: str = "",
        request_id: str | None = None,
    ) -> RawAsset:
        if self._audit is None or self._outbox is None or self._idempotency is None:
            raise RuntimeError("RawAsset 动作用例缺少审计/outbox/幂等依赖")
        if self._uow is None:
            raise RuntimeError("RawAsset 动作用例缺少事务单元")
        if action not in PUBLIC_RAW_ASSET_ACTIONS:
            raise DomainValidationError("未知状态动作")
        body_in = dict(payload or {})
        unknown = set(body_in) - _ACTION_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": body_in.get("reason"),
            "quarantine_codes": list(body_in.get("quarantine_codes") or []),
        }
        now = datetime.now(UTC)
        replay = self._guard.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        self.last_replayed = False
        if replay is not None:
            self.last_replayed = True
            try:
                replayed_id = UUID(str(replay.response_ref))
            except (TypeError, ValueError) as exc:
                raise ResourceNotFoundError("原始资产", replay.response_ref) from exc
            return self.get_asset(scope, replayed_id)

        current = self.get_asset(scope, asset_id)
        if current.version != expected_version:
            raise VersionConflictError("原始资产")

        from_status = current.status.value
        updated = current.apply_action(action, body_in, now=now)
        saved = self._assets.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="RAW_ASSET",
            resource_id=saved.asset_id,
            action=action,
            from_status=from_status,
            to_status=saved.status.value,
            reason=body_in.get("reason") if isinstance(body_in.get("reason"), str) else None,
            request_id=request_id,
            details={"code": saved.code, "quarantine_codes": list(saved.quarantine_codes)},
            event_type="ingestion.raw_asset.status_changed",
            event_payload={
                "asset_id": str(saved.asset_id),
                "code": saved.code,
                "action": action,
                "from_status": from_status,
                "to_status": saved.status.value,
                "version": saved.version,
            },
            occurred_at=now,
        )
        if action == "release-quarantine-or-revalidate":
            # 放行只是把状态退回 VALIDATING；不投递校验事件的话，资产会永远
            # 停在校验中——"放行"就成了一个假动作。校验 worker 消费该事件后
            # 重新读取对象、核对 Hash，再决定 VALID / QUARANTINED。
            self._outbox.enqueue(
                scope,
                make_outbox_event(
                    scope,
                    aggregate_id=saved.asset_id,
                    event_type=VALIDATION_EVENT_TYPE,
                    payload={
                        "asset_id": str(saved.asset_id),
                        "validation_job_id": None,
                        "upload_session_id": (
                            str(saved.upload_session_id)
                            if saved.upload_session_id is not None
                            else None
                        ),
                        "actor_user_id": str(scope.actor_id),
                    },
                    now=now,
                    aggregate_type="RAW_ASSET",
                    dispatch=True,
                ),
            )
        self._guard.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.asset_id),
            now=now,
        )
        self._uow.commit()
        return saved


def _asset_status(value: str | None) -> RawAssetStatus | None:
    if value is None:
        return None
    try:
        return RawAssetStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc
