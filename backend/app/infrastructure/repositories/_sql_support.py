"""设备域 SQLAlchemy 仓储共用的低层辅助。

这里只放仓储内部真正复用的 SQL/错误映射逻辑（分页上限、项目作用域查询、
乐观锁失败判定、唯一约束映射），不承载任何业务规则，也不作为跨模块工具层。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

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


def keyset_after_desc(
    model: type[Any],
    order_column: Any,
    id_column: Any,
    after_id: UUID | None,
    *,
    project_id: UUID | None = None,
) -> ColumnElement[bool] | None:
    """``(order_column, id)`` 倒序键集条件；``after_id`` 为空时返回 ``None``。

    对外游标只承载"最后一条记录的 ID"，同一排序值（例如同一毫秒）的并列
    无法用单列游标表达，因此这里用子查询取出游标行的排序值组成复合键集。
    与 ``annotation_quality_checks.keyset_after_created_desc`` /
    ``modelops_rows._after_clause`` 同构——那两处是既有域内私有副本，
    按增量迭代原则本次不迁移，收敛登记为技术债。

    ``project_id`` 必须传入：游标锚点子查询若不限定项目，别家项目的记录 ID
    会通过返回结果集间接泄露"该 ID 存在且时间是多少"。
    """
    if after_id is None:
        return None
    anchor = select(order_column).where(model.id == after_id)
    if project_id is not None:
        anchor = anchor.where(model.project_id == project_id)
    anchor_value = anchor.scalar_subquery()
    return or_(
        order_column < anchor_value,
        and_(order_column == anchor_value, id_column < after_id),
    )


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
