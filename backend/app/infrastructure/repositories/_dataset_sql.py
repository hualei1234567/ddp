"""数据集仓储共用的低层 SQL 辅助。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.datasets.errors import (
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope


def bounded_limit(limit: int, *, maximum: int = 200) -> int:
    if limit < 1 or limit > maximum:
        raise DomainValidationError(f"limit 必须在 1 到 {maximum} 之间")
    return limit


def enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def is_unique_violation(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return any(
        token in message
        for token in ("unique", "duplicate", "uq_", "duplicate key", "duplicate entry")
    )


def flush_or_duplicate(session: Session, message: str = "项目内业务键重复") -> None:
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
    if expected_version < 1:
        raise DomainValidationError("expected_version 必须大于等于 1")
    if entity_version not in {expected_version, expected_version + 1}:
        raise VersionConflictError("资源")


def scoped_select(model: type, scope: ProjectScope) -> Select[tuple[Any]]:
    return select(model).where(model.project_id == scope.project_id)


def raise_update_failure(
    session: Session,
    model: type[Any],
    scope: ProjectScope,
    resource_id: object,
    resource: str,
    expected_version: int,
) -> None:
    row = session.scalar(
        select(model).where(
            model.id == resource_id,
            model.project_id == scope.project_id,
        )
    )
    if row is None:
        raise ResourceNotFoundError(resource, resource_id)
    if row.version != expected_version:
        raise VersionConflictError(resource)
    raise InvalidStateTransitionError(resource, row.status, "update")
