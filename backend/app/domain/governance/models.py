"""治理域实体与对照规则（N9 规模化运营）。

三个概念，刻意分开：

- :class:`ResourceQuota`：**配置**。每个项目每种资源至多一条上限。
- :class:`ResourceUsage`：**测量结果**。``value=None`` 表示"测不到"，
  与 ``value=0``（真实为零）严格区分，这是三态原则的落点。
- :class:`QuotaComparison`：**对照**。报表条目；状态只由这里计算，
  不允许 API/前端各自推断 ``used >= limit``。

为什么测量结果必须自带来源与时间
--------------------------------
一个没有 ``measured_at`` 的数字在报表上无法判断是否过期；一个没有
``source`` 的数字无法追溯是"真实表计数"还是某次手工导数。因此
:class:`ResourceUsage` 在构造时就要求：有值 ⇒ 必须有 ``source`` 与带时区的
``measured_at``；无值 ⇒ 两者必须为空。这样"用一个裸 0 冒充失败"在领域层
根本构造不出来。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.governance.enums import QuotaStatus, ResourceKind
from app.domain.governance.errors import DomainValidationError

SOURCE_MAX_LENGTH = 64


def _required_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def _coerce_kind(value: object) -> ResourceKind:
    try:
        return ResourceKind(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in ResourceKind)
        raise DomainValidationError(f"resource_kind 取值非法，允许值：{allowed}") from exc


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def _aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区")
    return value


@dataclass(frozen=True, slots=True)
class ResourceQuota:
    """一个项目的单类资源上限。

    ``limit_value=0`` 合法且有明确语义：该类资源在本项目完全禁止新增。
    """

    quota_id: UUID
    project_id: UUID
    resource_kind: ResourceKind | str
    limit_value: int
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quota_id", _required_uuid(self.quota_id, "quota_id"))
        object.__setattr__(self, "project_id", _required_uuid(self.project_id, "project_id"))
        object.__setattr__(self, "resource_kind", _coerce_kind(self.resource_kind))
        object.__setattr__(
            self,
            "limit_value",
            _non_negative_int(self.limit_value, "limit_value"),
        )
        object.__setattr__(self, "version", _positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _aware(self.updated_at, "updated_at"))

    def with_limit(self, limit_value: int, *, now: datetime, actor_id: UUID) -> ResourceQuota:
        """生成修改上限后的实体；版本 +1 由仓储按 ``expected_version`` 落库。"""
        return replace(
            self,
            limit_value=_non_negative_int(limit_value, "limit_value"),
            updated_at=_aware(now, "now"),
            updated_by=actor_id,
        )


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """某一时刻可追溯的用量测量结果。

    ``value=None`` 是完整状态：没有可信统计。调用方**不得**把它渲染成 0。
    """

    resource_kind: ResourceKind | str
    value: int | None
    measured_at: datetime | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_kind", _coerce_kind(self.resource_kind))
        value = self.value
        if value is None:
            if self.measured_at is not None or self.source is not None:
                raise DomainValidationError("未测量时不得携带 measured_at 或 source")
            return
        object.__setattr__(
            self,
            "value",
            _non_negative_int(value, "value"),
        )
        measured_at = _aware(self.measured_at, "measured_at")
        if measured_at is None:
            raise DomainValidationError("已测量的用量必须带 measured_at")
        source = self.source
        if not isinstance(source, str) or not source.strip():
            raise DomainValidationError("已测量的用量必须标明 source")
        if len(source) > SOURCE_MAX_LENGTH:
            raise DomainValidationError(f"source 长度不得超过 {SOURCE_MAX_LENGTH}")
        object.__setattr__(self, "source", source.strip())


@dataclass(frozen=True, slots=True)
class QuotaComparison:
    """用量与配额的对照视图。

    ``status`` 是唯一权威判定；``remaining`` 在超限时截断为 0，
    ``exceeded`` 只在"已测量且配置了上限"时才是布尔值。
    """

    usage: ResourceUsage
    quota: ResourceQuota | None

    def __post_init__(self) -> None:
        if self.quota is not None and self.quota.resource_kind != self.usage.resource_kind:
            raise DomainValidationError("配额与用量的资源类型必须一致")

    @property
    def resource_kind(self) -> ResourceKind:
        return self.usage.resource_kind

    @property
    def used(self) -> int | None:
        return self.usage.value

    @property
    def limit(self) -> int | None:
        return None if self.quota is None else self.quota.limit_value

    @property
    def remaining(self) -> int | None:
        used, limit = self.used, self.limit
        if used is None or limit is None:
            return None
        return max(limit - used, 0)

    @property
    def exceeded(self) -> bool | None:
        """``None`` = 无法判断（未测量）。"""
        used, limit = self.used, self.limit
        if used is None:
            return None
        if limit is None:
            # 没有上限即"未超限"，这是可确定的结论。
            return False
        return used > limit

    @property
    def at_limit(self) -> bool:
        used, limit = self.used, self.limit
        return used is not None and limit is not None and used >= limit

    @property
    def status(self) -> QuotaStatus:
        used, limit = self.used, self.limit
        if used is None:
            return QuotaStatus.UNMEASURED
        if limit is None:
            return QuotaStatus.UNLIMITED
        if used > limit:
            return QuotaStatus.OVER_LIMIT
        if used == limit:
            return QuotaStatus.AT_LIMIT
        return QuotaStatus.OK


@dataclass(frozen=True, slots=True)
class UsageReport:
    """一个项目的用量—配额对照报表。"""

    project_id: UUID
    generated_at: datetime
    entries: tuple[QuotaComparison, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _required_uuid(self.project_id, "project_id"))
        object.__setattr__(self, "generated_at", _aware(self.generated_at, "generated_at"))


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """待落库的一次测量结果（系统级，非单项目）。"""

    project_id: UUID
    usage: ResourceUsage

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _required_uuid(self.project_id, "project_id"))
        if self.usage.value is None:
            raise DomainValidationError("快照只能保存已测量的用量")


__all__ = [
    "SOURCE_MAX_LENGTH",
    "QuotaComparison",
    "ResourceQuota",
    "ResourceUsage",
    "UsageReport",
    "UsageSnapshot",
]
