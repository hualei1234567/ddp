"""设备域 SQLAlchemy 仓储共用的低层辅助。

这里只放仓储内部真正复用的 SQL/错误映射逻辑（分页上限、项目作用域查询、
乐观锁失败判定、唯一约束映射），不承载任何业务规则，也不作为跨模块工具层。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.devices.errors import (
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories._optimistic_lock import (
    ensure_expected_version as _ensure_expected_version,
)

_T = TypeVar("_T")


def bounded_limit(limit: int, *, maximum: int = 100) -> int:
    if limit < 1 or limit > maximum:
        raise DomainValidationError(f"limit 必须在 1 到 {maximum} 之间")
    return limit


def enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite 不保留时区；读回时按 UTC 解释，避免领域层拒绝 naive 时间。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def is_unique_violation(exc: IntegrityError) -> bool:
    """识别唯一约束冲突；同时覆盖 SQLite 与 PostgreSQL 的驱动文案。"""
    message = str(exc.orig).lower()
    return any(
        token in message
        for token in ("unique", "duplicate", "uq_", "duplicate key", "duplicate entry")
    )


def flush_or_duplicate(
    session: Session,
    message: str = "项目内业务键或序列号重复",
) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        if is_unique_violation(exc):
            raise DuplicateResourceError(message) from exc
        raise


def ensure_initial_version(version: int) -> None:
    if version != 1:
        raise DomainValidationError("新增资源 version 必须为 1")


def ensure_expected_version(entity_version: int, expected_version: int) -> None:
    """前置乐观锁判定；实现只有一份，见 ``_optimistic_lock``。

    设备域的 ``VersionConflictError`` 需要 ``expected`` 参数，因此用它构造工厂。
    """
    _ensure_expected_version(
        entity_version,
        expected_version,
        invalid=DomainValidationError,
        conflict=lambda: VersionConflictError("资源", expected_version),
    )


def scoped_select(model: type[_T], scope: ProjectScope) -> Select[tuple[_T]]:
    return select(model).where(model.project_id == scope.project_id)  # type: ignore[attr-defined]


def raise_update_failure(
    session: Session,
    model: type[Any],
    scope: ProjectScope,
    resource_id: UUID,
    resource: str,
    expected_version: int,
    *,
    expected_status: str | None = None,
) -> None:
    """条件 UPDATE 的 rowcount 为 0 时，区分“不存在 / 版本过期 / 状态已变化”。

    查询始终带 ``project_id``，避免跨项目资源被误报为版本冲突而泄露存在性。
    """
    row = session.scalar(
        select(model).where(
            model.id == resource_id,
            model.project_id == scope.project_id,
        )
    )
    if row is None:
        raise ResourceNotFoundError(resource, resource_id)
    if row.version != expected_version:
        raise VersionConflictError(resource, expected_version)
    if expected_status is not None and row.status != expected_status:
        raise InvalidStateTransitionError(resource, row.status, expected_status)
    # 版本与预期一致却未命中：保守地按并发冲突处理，绝不静默成功。
    raise VersionConflictError(resource, expected_version)
