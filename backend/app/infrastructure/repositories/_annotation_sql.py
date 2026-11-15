"""标注仓储共用的低层 SQL 辅助。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.annotation.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories._optimistic_lock import (
    ensure_expected_version as _ensure_expected_version,
)


def enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def ensure_initial_version(version: int) -> None:
    if version != 1:
        raise DomainValidationError("新增资源 version 必须为 1")


def ensure_expected_version(entity_version: int, expected_version: int) -> None:
    """前置乐观锁判定；实现只有一份，见 ``_optimistic_lock``。

    入参约定：``entity_version`` 与 ``expected_version`` 都是**当前版本**
    （不是自增后的候选版本）。判定严格相等——SQL 的
    ``WHERE version = expected_version`` 与 ``raise_update_failure`` 也是严格相等，
    前置判定与真正生效的条件保持一致，避免"预检查放过、写库才失败"的困惑。
    """
    _ensure_expected_version(
        entity_version,
        expected_version,
        invalid=DomainValidationError,
        conflict=lambda: VersionConflictError("资源"),
    )


def flush_or_duplicate(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        message = str(exc.orig).lower()
        if any(token in message for token in ("unique", "duplicate", "uq_")):
            raise DuplicateResourceError("项目内业务键重复") from exc
        raise


def scoped_select(model: type, scope: ProjectScope) -> Select:
    return select(model).where(model.project_id == scope.project_id)  # type: ignore[attr-defined]


def raise_update_failure(
    session: Session,
    model: type,
    scope: ProjectScope,
    resource_id: UUID,
    resource: str,
) -> None:
    row = session.scalar(
        select(model).where(
            model.id == resource_id,
            model.project_id == scope.project_id,
        )
    )
    if row is None:
        raise ResourceNotFoundError(resource, resource_id)
    raise VersionConflictError(resource)


def json_list_of_str(values: object) -> list[str]:
    if not isinstance(values, list | tuple):
        return []
    return [str(item) for item in values]


def json_list_of_dict(values: object) -> list[dict[str, object]]:
    if not isinstance(values, list | tuple):
        return []
    return [dict(item) for item in values if isinstance(item, dict)]
