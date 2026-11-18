"""采集任务用例：项目范围查询、创建/修改、具名状态动作、审计与幂等。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.application.collection.asset_references import (
    assert_task_asset_references,
    assert_task_location_reference,
)
from app.application.collection.audit import record_change
from app.application.collection.commands import NewCollectionTask
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import (
    AssignmentDirectory,
    AuditRepository,
    CollectionAssetDirectory,
    CollectionBatchRepository,
    CollectionTaskRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.locations.ports import LocationDirectory
from app.domain.collection.enums import (
    PUBLIC_TASK_ACTIONS,
    CollectionTaskStatus,
)
from app.domain.collection.errors import (
    ConflictError,
    DomainValidationError,
    ForbiddenOperationError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.collection.models import CollectionTask
from app.domain.collection.state_machines import (
    assert_task_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

if TYPE_CHECKING:
    from typing import Protocol

    class EpisodeCountDirectory(Protocol):
        def count_by_task(self, scope: ProjectScope, task_id: UUID) -> dict[str, int]: ...

        def counts_by_task(
            self, scope: ProjectScope, task_ids: list[UUID]
        ) -> dict[UUID, dict[str, int]]: ...


_TASK_PATCHABLE_FIELDS = frozenset(
    {
        "name",
        "priority",
        "sop_version",
        "scheduled_start",
        "scheduled_end",
        "target_episode_count",
        "target_duration_sec",
        "location_id",
    }
)
_ACTION_FIELDS = frozenset(
    {
        "reason",
        "assigned_to",
        "assigned_robot_id",
        "assigned_device_ids",
        "assigned_sensor_ids",
        "location_id",
        "scheduled_start",
        "scheduled_end",
        "waiver_reason",
    }
)
_RESOURCE_TYPE = "COLLECTION_TASK"


class CollectionTaskUseCases:
    def __init__(
        self,
        tasks: CollectionTaskRepository,
        batches: CollectionBatchRepository,
        directory: AssignmentDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        *,
        asset_directory: CollectionAssetDirectory,
        location_directory: LocationDirectory,
        episode_counts: EpisodeCountDirectory | None = None,
    ) -> None:
        self._tasks = tasks
        self._batches = batches
        self._directory = directory
        self._asset_directory = asset_directory
        self._locations = location_directory
        # Episode 计数（submitted/accepted）此前没有任何写入点：complete 的
        # "验收数量达标"判定永远不成立，只能靠豁免。每次动作前用 Episode 事实
        # 重新算一遍并随保存写回，比"状态迁移时 +1"更不容易长期偏移。
        self._episode_counts = episode_counts
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def list_tasks(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        assigned_to: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CollectionTask]:
        bounded_limit = normalize_limit(limit)
        items = self._tasks.list(
            scope,
            status=_task_status(status),
            assigned_to=assigned_to,
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        counts = self._episode_counts_for(scope, [item.task_id for item in items])
        items = [self._apply_counts(item, counts) for item in items]
        return build_page(items, limit=bounded_limit, id_of=lambda item: item.task_id)

    def get_task(self, scope: ProjectScope, task_id: UUID) -> CollectionTask:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("采集任务", task_id)
        # 计数以 Episode 事实为准：列表、详情、动作三条路径口径必须一致，
        # 否则会出现"详情显示 1 条已验收、complete 却说未达标"的自相矛盾。
        return self._with_episode_counts(scope, task)

    def create_task(
        self,
        scope: ProjectScope,
        command: NewCollectionTask,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CollectionTask:
        key = validate_key(idempotency_key)
        body = asdict(command)
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
            return self._replay(scope, replay.response_ref)

        # COL-002：四个素材引用必须同项目存在且版本一致。放在构造实体之前，
        # 让 404/422 先于"外键 IntegrityError"出现，客户端能按 code 分支。
        assert_task_asset_references(
            self._asset_directory,
            scope,
            plan_id=command.plan_id,
            sop_id=command.sop_id,
            sop_version=command.sop_version,
            scenario_id=command.scenario_id,
            action_spec_id=command.action_spec_id,
            action_spec_version=command.action_spec_version,
        )
        assert_task_location_reference(
            self._locations,
            scope,
            location_id=command.location_id,
        )

        task = CollectionTask(
            task_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            priority=command.priority,
            target_episode_count=command.target_episode_count,
            target_duration_sec=command.target_duration_sec,
            requirement_id=command.requirement_id,
            plan_id=command.plan_id,
            sop_id=command.sop_id,
            sop_version=command.sop_version,
            scenario_id=command.scenario_id,
            action_spec_id=command.action_spec_id,
            action_spec_version=command.action_spec_version,
            scheduled_start=command.scheduled_start,
            scheduled_end=command.scheduled_end,
            location_id=command.location_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._tasks.add(scope, task)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.task_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={"code": created.code},
            event_type="collection.task.created",
            event_payload={
                "task_id": str(created.task_id),
                "code": created.code,
                "status": created.status.value,
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
            response_ref=str(created.task_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_task(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> CollectionTask:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - _TASK_PATCHABLE_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get_task(scope, task_id)
        if current.version != expected_version:
            raise VersionConflictError("采集任务")
        if current.status is not CollectionTaskStatus.DRAFT:
            raise ResourceImmutableError("仅 DRAFT 任务允许修改核心字段")

        # ``sop_version`` 是可改字段，但改完仍必须是"所引用素材那一行的版本"。
        # 复用创建时的同一份校验，避免 PATCH 成为绕过版本一致性的后门
        # （否则可以把 v1 改成不存在的 v9）。其余三个引用不可经 PATCH 修改。
        if "sop_version" in changes:
            assert_task_asset_references(
                self._asset_directory,
                scope,
                plan_id=current.plan_id,
                sop_id=current.sop_id,
                sop_version=changes["sop_version"],  # type: ignore[arg-type]
                scenario_id=current.scenario_id,
                action_spec_id=current.action_spec_id,
                action_spec_version=current.action_spec_version,
            )

        if "location_id" in changes:
            assert_task_location_reference(
                self._locations,
                scope,
                location_id=_optional_uuid(changes["location_id"], "location_id"),
            )

        now = _utcnow()
        updated = replace(
            current,
            **dict(changes),
            updated_at=now,
            updated_by=scope.actor_id,
        )
        saved = self._tasks.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.task_id,
            action="update",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type="collection.task.updated",
            event_payload={
                "task_id": str(saved.task_id),
                "fields": sorted(changes),
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        task_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CollectionTask:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            **dict(payload or {}),
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
            return self._replay(scope, replay.response_ref)

        unknown = set(payload or {}) - _ACTION_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许的动作字段")
        if action not in PUBLIC_TASK_ACTIONS:
            raise DomainValidationError("未知状态动作")
        action_name = validate_action_name(action)

        current = self.get_task(scope, task_id)
        if current.version != expected_version:
            raise VersionConflictError("采集任务")
        # 先校验状态机，避免非法迁移被业务权限错误掩盖。
        assert_task_action(current.status, action_name)
        if action_name == "approve" and current.created_by == scope.actor_id:
            raise ForbiddenOperationError("任务创建人不得审批自己的任务")
        if action_name == "assign":
            self._validate_assignment(scope, current, payload or {})
        if action_name == "complete":
            self._validate_completion(scope, current, payload or {})

        updated = current.apply_action(action_name, payload, now=now)
        updated = replace(updated, updated_by=scope.actor_id)
        saved = self._tasks.save(scope, updated, expected_version=expected_version)

        cancelled_batches = 0
        if action_name == "cancel":
            cancelled_batches = self._cancel_batches(
                scope,
                saved.task_id,
                reason=_reason(payload),
                request_id=request_id,
                now=now,
            )

        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.task_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=_reason(payload),
            request_id=request_id,
            details={"cancelled_batches": cancelled_batches},
            event_type="collection.task.status_changed",
            event_payload={
                "task_id": str(saved.task_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
                "version": saved.version,
                "cancelled_batches": cancelled_batches,
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
            response_ref=str(saved.task_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _episode_counts_for(
        self,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> dict[UUID, dict[str, int]]:
        if self._episode_counts is None:
            return {}
        return self._episode_counts.counts_by_task(scope, task_ids)

    def _apply_counts(
        self,
        task: CollectionTask,
        counts: Mapping[UUID, dict[str, int]],
    ) -> CollectionTask:
        if task.task_id is None:
            return task
        bucket = counts.get(task.task_id)
        if bucket is None:
            return task
        return replace(
            task,
            submitted_episode_count=int(bucket["total"]),
            accepted_episode_count=int(bucket["accepted"]),
        )

    def _with_episode_counts(
        self,
        scope: ProjectScope,
        task: CollectionTask,
    ) -> CollectionTask:
        if self._episode_counts is None or task.task_id is None:
            return task
        counts = self._episode_counts.count_by_task(scope, task.task_id)
        return replace(
            task,
            submitted_episode_count=int(counts["total"]),
            accepted_episode_count=int(counts["accepted"]),
        )

    def _validate_assignment(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        payload: Mapping[str, object],
    ) -> None:
        assigned_to = payload.get("assigned_to", task.assigned_to)
        if assigned_to is None:
            raise DomainValidationError("分配动作必须指定执行人")
        if not isinstance(assigned_to, UUID) or not self._directory.user_is_member(
            scope, assigned_to
        ):
            raise ResourceNotFoundError("项目成员", assigned_to)

        robot_id = payload.get("assigned_robot_id", task.assigned_robot_id)
        if robot_id is not None:
            if not isinstance(robot_id, UUID):
                raise DomainValidationError("assigned_robot_id 必须是 UUID")
            status = self._directory.robot_status(scope, robot_id)
            if status is None:
                raise ResourceNotFoundError("机器人", robot_id)
            if status != "ACTIVE":
                raise DomainValidationError("机器人当前不可用于分配")

        for field_name, lookup in (
            ("assigned_device_ids", self._directory.device_status),
            ("assigned_sensor_ids", self._directory.sensor_status),
        ):
            raw_ids = payload.get(field_name, getattr(task, field_name))
            if raw_ids is None:
                continue
            for resource_id in raw_ids:
                if not isinstance(resource_id, UUID):
                    raise DomainValidationError(f"{field_name} 只能包含 UUID")
                status = lookup(scope, resource_id)
                if status is None:
                    raise ResourceNotFoundError("分配资源", resource_id)
                if status != "ACTIVE":
                    raise DomainValidationError("分配资源当前不可用")

        # ``location_id`` 也是动作可提交字段（``_ACTION_FIELDS``），
        # 必须与创建/PATCH 走同一份场地校验，否则 assign 就成了绕过校验的后门。
        if "location_id" in payload:
            assert_task_location_reference(
                self._locations,
                scope,
                location_id=_optional_uuid(payload["location_id"], "location_id"),
            )

    def _validate_completion(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        payload: Mapping[str, object],
    ) -> None:
        blockers = self._batches.list_non_terminal(scope, task_id=task.task_id)
        if blockers:
            raise ConflictError("存在未验收或未取消的批次")
        waiver = payload.get("waiver_reason")
        if (
            task.target_episode_count > 0
            and task.accepted_episode_count < task.target_episode_count
            and not waiver
        ):
            raise DomainValidationError("验收数量未达标且未提供豁免原因")

    def _cancel_batches(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        reason: str | None,
        request_id: str | None,
        now: datetime,
    ) -> int:
        cancelled = 0
        for batch in self._batches.list_non_terminal(scope, task_id=task_id):
            updated = batch.apply_action("cancel", {"reason": reason}, now=now)
            updated = replace(updated, updated_by=scope.actor_id)
            saved = self._batches.save(scope, updated, expected_version=batch.version)
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type="COLLECTION_BATCH",
                resource_id=saved.batch_id,
                action="cancel",
                from_status=batch.status.value,
                to_status=saved.status.value,
                reason=reason,
                request_id=request_id,
                details={"cascade_from_task": str(task_id)},
                event_type="collection.batch.status_changed",
                event_payload={
                    "batch_id": str(saved.batch_id),
                    "task_id": str(task_id),
                    "from_status": batch.status.value,
                    "to_status": saved.status.value,
                    "action": "cancel",
                    "cascade": True,
                },
                occurred_at=now,
            )
            cancelled += 1
        return cancelled

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> CollectionTask:
        if not response_ref:
            raise ResourceNotFoundError("采集任务", response_ref)
        try:
            task_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("采集任务", response_ref) from exc
        return self.get_task(scope, task_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _task_status(value: str | None) -> CollectionTaskStatus | None:
    if value is None:
        return None
    try:
        return CollectionTaskStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _reason(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("reason")
    return value if isinstance(value, str) else None


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    """把 PATCH 请求体里的可选 UUID 归一；``None`` 表示清空该引用。"""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是 UUID") from exc
