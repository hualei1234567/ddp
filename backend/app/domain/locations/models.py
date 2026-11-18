"""场地实体（N9 多基地）。

背景（为什么必须新建这个聚合）
------------------------------
``robots.location_id`` / ``devices.location_id`` / ``collection_tasks.location_id``
从迁移 0003 / 0007 起就存在，但**从来没有 ``locations`` 表，也没有任何外键**。
三个 UUID 列可以写入任意值——跨项目 ID、根本不存在的"幽灵引用"，数据库层
完全无法拒绝。后果与 COL-002 同型：系统无法可靠回答"这个机器人部署在哪个
基地""这批采集在哪个场地做的"。本模块补齐场地实体，迁移 0042 补齐表与外键。

为什么独立成 ``app/domain/locations/`` 而不是塞进 devices 或 collection
-----------------------------------------------------------------------
场地被**两个**聚合引用：设备域（Robot / Device 的部署地点）与采集域
（CollectionTask 的执行场地）。放进任一域，另一域就必须导入它的内部实现，
违反"跨模块只通过公开 Application API / 契约 / 事件"的边界规则。
场地本身也不是设备或采集的子概念——它是独立的组织维度（多基地运营），
未来还会被成本核算、场地利用率等消费。因此新建平级模块，两域都只依赖它。

本阶段刻意**没有**的东西（都是"加一个无人读取的字段"的债，不是遗漏）
------------------------------------------------------------------
- ``status``（启用/停用）：当前没有任何读取方按状态过滤场地；采集任务与设备
  只校验"存在且同项目"。加状态列就必须同时设计状态动作、过滤参数与前端标签，
  否则会重复 COL-002 素材 ``ACTIVE`` / ``ARCHIVED`` 至今不可达的缺陷。
- ``timezone``：见下。
- ``metadata``（自由 JSON）：没有消费方，形状无处校验，等于契约黑洞。
  需要承载额外字段时再按明确字段加列（可校验），而不是先开一个 JSON 口袋。

为什么**不**记录 ``timezone``（评估结论）
----------------------------------------
多基地意味着跨时区，但"同一物理时刻被算成不同时间"这个风险已由既有实现
闭环，不需要再在场地表上重复一份：

1. Episode 的 ``start_time_ns`` / ``end_time_ns`` / ``duration_ns`` 是
   **UTC 纳秒**（``docs/architecture/阶段2-采集上传RawAsset-Episode契约-v1.md``
   §时间语义），与场地时区无关；
2. ``Episode.time_base`` 携带 ``time_domain`` / ``clock_source`` /
   ``offset_to_utc_ns`` / ``drift_ppm`` / ``sync_status``，其
   ``sync_status`` 为 ``UNVERIFIED`` / ``FAILED`` 时
   ``validate_time_base`` 直接拒绝创建 Episode（fail-closed）。也就是说：
   设备上报时间若不带明确的时区/时钟语义，数据根本进不来，不存在"悄悄按本地
   时间落库"的路径；
3. 采集任务与设备的 ``scheduled_start`` / ``scheduled_end`` 在领域层经
   ``optional_aware_datetime`` 强制带时区并归一到 UTC——存的是绝对时刻，
   展示成本地时间只是前端格式化。

在场地表上加一个无人读取的 IANA 时区字符串，反而会制造"两处时区真相"
（场地声称 + 数据实际 time_base），不一致时无从判定以谁为准。

领域层只保证单实体不变量（必填、长度、类型、``kind`` 取值、``version`` 为正）；
"引用必须同项目存在"是跨对象前置条件，由应用层校验 + 数据库复合外键兜底。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.locations.enums import DEFAULT_LOCATION_KIND, LocationKind
from app.domain.locations.errors import DomainValidationError
from app.domain.locations.validation import (
    ADDRESS_MAX_LENGTH,
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    NOTES_MAX_LENGTH,
    non_negative_int,
    normalized_text,
    optional_text,
    positive_int,
    required_uuid,
)

# PATCH 允许修改的字段白名单。``code`` 不在其中：它与 ``(project_id, code)``
# 唯一约束绑定，且被任务/设备的引用语义间接依赖；改 code 属于"换一个场地"，
# 应当新建行后重新指向，而不是原地改名。``kind`` 允许改（层级判断错时修正）。
PATCHABLE_FIELDS = frozenset({"name", "kind", "address", "notes"})


@dataclass(frozen=True, slots=True)
class Location:
    """项目内的一个采集场地（基地 / 房间 / 实验室）。"""

    location_id: UUID
    project_id: UUID
    code: str
    name: str
    kind: LocationKind | str = DEFAULT_LOCATION_KIND
    address: str | None = None
    notes: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "location_id", required_uuid(self.location_id, "location_id"))
        object.__setattr__(self, "project_id", required_uuid(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(self, "kind", _kind(self.kind))
        object.__setattr__(
            self,
            "address",
            optional_text(self.address, "address", max_length=ADDRESS_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "notes",
            optional_text(self.notes, "notes", max_length=NOTES_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    def apply_patch(self, changes: dict[str, object], *, now: datetime, actor_id: UUID) -> Location:
        """按白名单应用 PATCH；未知字段一律拒绝，不做"忽略未知字段"的宽松处理。

        显式传 ``None`` 表示把可空字段清空（``address`` / ``notes``），
        与"未提供该字段"（不在 ``changes`` 里）严格区分——否则调用方无法清空
        一个填错的地址。
        """
        unknown = set(changes) - PATCHABLE_FIELDS
        if unknown:
            raise DomainValidationError("请求包含不允许修改的字段")
        if not changes:
            raise DomainValidationError("PATCH 至少需要一个字段")
        return replace(self, **changes, updated_at=now, updated_by=actor_id)


def _kind(value: object) -> LocationKind:
    try:
        return LocationKind(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in LocationKind)
        raise DomainValidationError(f"kind 取值非法，允许值：{allowed}") from exc


@dataclass(frozen=True, slots=True)
class LocationResourceCounts:
    """某场地在当前项目内的只读资源计数。

    这是**派生读数**，不是主数据：不落库、不参与状态机、不做计费。
    三个计数都允许 0——空基地必须如实返回 0，调用方才能区分
    "这个基地确实没有设备"与"服务端没返回这个基地"。
    """

    device_count: int = 0
    sensor_count: int = 0
    collection_task_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "device_count",
            non_negative_int(self.device_count, "device_count"),
        )
        object.__setattr__(
            self,
            "sensor_count",
            non_negative_int(self.sensor_count, "sensor_count"),
        )
        object.__setattr__(
            self,
            "collection_task_count",
            non_negative_int(self.collection_task_count, "collection_task_count"),
        )


# 单例空读数：调用方对没有资源的场地统一回落到它，避免每次 new 一个对象。
EMPTY_RESOURCE_COUNTS = LocationResourceCounts()


__all__ = [
    "EMPTY_RESOURCE_COUNTS",
    "PATCHABLE_FIELDS",
    "Location",
    "LocationResourceCounts",
]
