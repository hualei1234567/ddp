"""标注仓储共用的低层 SQL 辅助。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.annotation.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.submission import VISIBLE_DRAFT_STATUS
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


def keyset_after(column_id, after_id: UUID | None):
    """键集游标条件：``id > after_id``；``after_id`` 为空时返回 ``None``。

    与标注域既有列表一致（``order_by(id)`` + ``id > after_id``），
    避免深度 offset 与并发插入导致的错页。
    """
    if after_id is None:
        return None
    return column_id > after_id


def visible_submission_clause(
    status_column,
    annotator_column,
    actor_id: UUID,
) -> ColumnElement[bool]:
    """草稿可见性下推到 SQL：``status != 'DRAFT' OR annotator_id = actor``。

    与领域层 ``submission_visible_to`` 语义一致（规则唯一来源是
    ``VISIBLE_DRAFT_STATUS``）；必须下推到查询里，否则先取 ``limit+1``
    再在内存过滤会让他人草稿占名额，出现"页内条数不足却仍有下一页"。
    """
    return or_(
        status_column != VISIBLE_DRAFT_STATUS,
        annotator_column == actor_id,
    )


def keyset_after_created_desc(
    model: type,
    created_column,
    id_column,
    after_id: UUID | None,
):
    """``(created_at, id)`` 倒序键集条件；``after_id`` 为空时返回 ``None``。

    IAA 报告列表沿用既有的 ``created_at DESC`` 语义，单列游标无法表达同秒并列，
    因此用子查询取出游标行的 ``created_at`` 组成复合键集。本函数只做 SQL 条件
    构造，与 ``modelops_rows._after_clause`` 同构；按模块边界规范不跨模块导入
    其它域的内部辅助，故在本域保留一份（语义已对齐）。
    """
    if after_id is None:
        return None
    created = select(model.created_at).where(model.id == after_id).scalar_subquery()
    return or_(
        created_column < created,
        and_(created_column == created, id_column < after_id),
    )


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
