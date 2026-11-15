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
from app.infrastructure.repositories._optimistic_lock import (
    ensure_expected_version as _ensure_expected_version,
)


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
    """前置乐观锁判定；实现只有一份，见 ``_optimistic_lock``。

    **数据集域仍然保留 ``accept_candidate=True``，没有改成严格相等。** 该域的
    调用方两种形态都在用，逐一核对过：

    - ``dataset_repositories.save()`` 收到的实体已自增（``update_dataset`` /
      ``version_actions`` 都 ``row_version=current+1``），而 ``expected_version``
      仍是当前版本 —— 实体版本领先一等；
    - ``training_requests`` 反过来显式传 ``expected_version=request.row_version - 1``
      —— 换算后实体版本同样领先一等；
    - ``version_actions`` 里也有 ``expected_version=building.row_version`` 的调用，
      —— 此时两值相等。

    所以本域的实际契约是"当前版本"或"已自增候选版本"二者皆可。
    改成严格相等会让上面前两类调用全部 409。

    并发安全不依赖这层前置判定：真正生效的是 SQL 的
    ``WHERE version = expected_version`` 与未命中时的 ``raise_update_failure``
    （后者按严格相等重新读行判定）。

    （本 docstring 刻意不逐字写出判定表达式；``tests/unit/test_optimistic_lock.py``
    用 ``inspect.getsource`` 断言包装函数里不得出现判定本体。）
    """
    _ensure_expected_version(
        entity_version,
        expected_version,
        invalid=DomainValidationError,
        conflict=_conflict,
        # 数据集域同时存在"传当前版本"与"传已自增候选版本"两种调用方。
        accept_candidate=True,
    )


def _conflict() -> VersionConflictError:
    return VersionConflictError("资源")


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
