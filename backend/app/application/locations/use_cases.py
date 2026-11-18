"""场地域用例：创建 / 列表 / 详情 / 修改（N9 多基地）。

为什么**不做删除**
------------------
``robots`` / ``devices`` / ``collection_tasks`` 三处 ``(project_id, location_id)``
复合外键都指向本表，删除被引用的场地会被数据库直接拒绝（``IntegrityError``）。
把它做成一个"看运气成功"的接口没有意义：调用方无法从成功/失败反推原因，
只能解析数据库原文。

另一个可行方向是"只允许删未被引用的"，但那需要额外的引用计数查询与并发窗口
处理，而当前**没有任何业务方需要删场地**——场地是组织维度，随项目级联删除
即可（``project_id`` 上的 ``ON DELETE CASCADE``）。因此本阶段只提供
create / list / get / update，需求出现时再按"未引用才可删 + 引用方清单"补，
而不是现在先放一个会因外键失败而 500 的删除入口。

为什么 update 带 ``If-Match`` 乐观锁
------------------------------------
场地会被多个设备与任务引用，并发改 ``name`` / ``address`` 时若无版本校验，
后写者静默覆盖前者。这与任务/批次的既有口径一致；素材类（COL-002）没有
update 路径，因此不存在可参照的"无锁素材"先例。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.idempotency import IdempotencyGuard
from app.application.locations.commands import NewLocation
from app.application.locations.overview import SiteOverviewEntry
from app.application.locations.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.locations.ports import (
    AuditRepository,
    IdempotencyRepository,
    LocationRepository,
    OutboxRepository,
    SiteResourceSummaryRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.locations.enums import LocationKind
from app.domain.locations.errors import (
    DomainValidationError,
    DuplicateResourceError,
    IdempotencyKeyReusedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.locations.models import (
    EMPTY_RESOURCE_COUNTS,
    PATCHABLE_FIELDS,
    Location,
)

_LOCATION_RESOURCE_TYPE = "LOCATION"
_LOCATION_LABEL = "采集场地"

# 事件名必须是字面量：``scripts/check_outbox_events.py`` 用 AST 提取事件类型，
# f-string 会被判失败（R-13）。两者都是**留痕类**（
# ``docs/architecture/outbox事件语义界定-v1.0.md``），无消费者。
LOCATION_CREATED_EVENT = "location.created"
LOCATION_UPDATED_EVENT = "location.updated"


class LocationUseCases:
    def __init__(
        self,
        locations: LocationRepository,
        site_resources: SiteResourceSummaryRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._locations = locations
        self._site_resources = site_resources
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(
            idempotency,
            invalid_key_error=DomainValidationError,
            reused_error=IdempotencyKeyReusedError,
        )
        self._uow = uow

    def list_locations(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[Location]:
        bounded = normalize_limit(limit)
        items = self._locations.list(
            scope,
            kind=_kind_filter(kind),
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=lambda item: item.location_id)

    def list_site_overview(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[SiteOverviewEntry]:
        """多基地资源只读概览：场地分页 + 三个 SQL 聚合计数。

        与 ``list_locations`` 共用同一套游标/上限和 ``kind`` 校验，避免出现
        "列表接受 SITE、概览接受 site"这类两套口径。计数只针对本页场地，
        查询规模被 ``limit``（≤100）约束，不做全项目扫描。

        计数为 0 是合法值：空基地必须出现在结果里并如实返回 0。
        """
        bounded = normalize_limit(limit)
        items = self._locations.list(
            scope,
            kind=_kind_filter(kind),
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        page = build_page(items, limit=bounded, id_of=lambda item: item.location_id)
        counts = self._site_resources.count_by_location(
            scope,
            location_ids=[item.location_id for item in page.items],
        )
        return Page(
            items=[
                SiteOverviewEntry(
                    location=item,
                    resources=counts.get(item.location_id, EMPTY_RESOURCE_COUNTS),
                )
                for item in page.items
            ],
            next_cursor=page.next_cursor,
        )

    def get_location(self, scope: ProjectScope, location_id: UUID) -> Location:
        location = self._locations.get(scope, location_id)
        if location is None:
            raise ResourceNotFoundError(_LOCATION_LABEL, location_id)
        return location

    def create_location(
        self,
        scope: ProjectScope,
        command: NewLocation,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Location:
        key = self._idempotency.validate_key(idempotency_key)
        body: Mapping[str, object] = {
            "code": command.code,
            "name": command.name,
            "kind": command.kind,
            "address": command.address,
            "notes": command.notes,
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get_location(scope, _decoded_ref(replay.response_ref))

        # 先查重给出可读 409，再落库：唯一约束是最后一道兜底，不应成为
        # 客户端唯一能看到的反馈（``IntegrityError`` 原文不可读且无法按 code 分支）。
        if self._locations.get_by_code(scope, command.code) is not None:
            # 不把 code 原值拼进消息：它是客户端输入，回显会进入日志。
            raise DuplicateResourceError("项目内场地 code 重复")

        location = Location(
            location_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            kind=command.kind,
            address=command.address,
            notes=command.notes,
            version=1,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._locations.add(scope, location)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_LOCATION_RESOURCE_TYPE,
            resource_id=created.location_id,
            action="create",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={"code": created.code, "kind": created.kind.value},
            event_type=LOCATION_CREATED_EVENT,
            event_payload={
                "location_id": str(created.location_id),
                "code": created.code,
                "kind": created.kind.value,
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
            response_ref=str(created.location_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_location(
        self,
        scope: ProjectScope,
        location_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
        request_id: str | None = None,
    ) -> Location:
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        unknown = set(changes) - PATCHABLE_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        current = self.get_location(scope, location_id)
        if current.version != expected_version:
            raise VersionConflictError(_LOCATION_LABEL)

        now = datetime.now(UTC)
        updated = current.apply_patch(
            dict(changes),
            now=now,
            actor_id=scope.actor_id,
        )
        saved = self._locations.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_LOCATION_RESOURCE_TYPE,
            resource_id=saved.location_id,
            action="update",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={"fields": sorted(changes)},
            event_type=LOCATION_UPDATED_EVENT,
            event_payload={
                "location_id": str(saved.location_id),
                "fields": sorted(changes),
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved


def _kind_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return LocationKind(value).value
    except ValueError as exc:
        raise DomainValidationError("kind 取值非法") from exc


def _decoded_ref(response_ref: str | None) -> UUID:
    if not response_ref:
        raise ResourceNotFoundError(_LOCATION_LABEL, response_ref)
    try:
        return UUID(response_ref)
    except ValueError as exc:
        raise ResourceNotFoundError(_LOCATION_LABEL, response_ref) from exc


__all__ = [
    "LOCATION_CREATED_EVENT",
    "LOCATION_UPDATED_EVENT",
    "LocationUseCases",
]
