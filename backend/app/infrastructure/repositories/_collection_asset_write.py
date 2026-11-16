"""采集素材仓储的写路径公共实现（N8）。

为什么单独成私有模块
--------------------
四类素材（方案 / SOP / 场景 / 动作库）的 **写路径规则完全相同**：

- 乐观锁：``WHERE id = ? AND project_id = ? AND version = expected_version``，
  未命中时区分 404 与 409；
- 删除：存在性（404）→ 仅 DRAFT（409）→ 未被任务引用（409）→ 删除；
- 语句再次携带状态条件，避免"判定时是 DRAFT、删除时已 ACTIVE"的并发窗口。

只有"用哪个 Model、哪个 id 列、哪个任务引用列、哪几个可改字段"不同。
逐份复制会让上面三条规则出现四个变体——正是"规则只允许存在一处权威实现"
要避免的形态。放在私有模块（前导下划线）是因为它不是端口的一部分，
外部只应通过 :mod:`app.application.collection.ports` 声明的仓储协议使用。

为什么不复用 ORM 基类：四类素材的 SQLAlchemy 模型类型不同，硬套泛型会让
pytest / mypy 读不出真实字段，反而比直白参数更难核对（与
``collection_assets.py`` 的同期论证一致）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import Session

from app.domain.collection.asset_lifecycle import assert_delete_allowed
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import ResourceImmutableError, ResourceNotFoundError
from app.domain.collection.validation import coerce_enum
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.repositories._collection_sql import raise_update_failure


def save_asset_update(
    session: Session,
    scope: ProjectScope,
    *,
    model: type,
    asset_id: UUID,
    values: Mapping[str, object],
    expected_version: int,
    resource_name: str,
) -> None:
    """带乐观锁的局部更新；未命中时按"行是否存在"区分 404 / 409。

    ``values`` 只包含**调用方证明可改**的字段，且不得含 ``code`` /
    ``version`` / 审计列——这些由仓储统一维护，调用方无权直接指定。
    """
    now = datetime.now(UTC)
    result = session.execute(
        update(model)
        .where(
            model.id == asset_id,  # type: ignore[attr-defined]
            model.project_id == scope.project_id,  # type: ignore[attr-defined]
            model.version == expected_version,  # type: ignore[attr-defined]
        )
        .values(
            **dict(values),
            updated_at=now,
            updated_by=scope.actor_id,
            version=expected_version + 1,
        )
    )
    if result.rowcount == 0:
        raise_update_failure(session, model, scope, asset_id, resource_name)


def delete_asset_draft(
    session: Session,
    scope: ProjectScope,
    *,
    model: type,
    asset_id: UUID,
    task_column: object,
    resource_name: str,
) -> None:
    """删除 ``DRAFT`` 素材；顺序固定为 404 → 状态 409 → 引用 409 → 删除。

    存在性必须最先：跨项目 ID 与不存在的 ID 都要 404，不能因为"状态不对"
    先返回 409——那会泄漏"该 ID 在本项目存在"。
    """
    status = session.scalar(
        select(model.status).where(  # type: ignore[attr-defined]
            model.id == asset_id,  # type: ignore[attr-defined]
            model.project_id == scope.project_id,  # type: ignore[attr-defined]
        )
    )
    if status is None:
        raise ResourceNotFoundError(resource_name, asset_id)
    # 状态闸门复用领域层实现："只有 DRAFT 能删"这条规则不能有第二份。
    # 未知取值会被 ``coerce_enum`` 拒绝（不默认成功）。
    assert_delete_allowed(
        coerce_enum(status, CollectionAssetStatus, "status"),
        resource_name=resource_name,
    )
    _assert_no_task_reference(
        session,
        scope,
        asset_id=asset_id,
        task_column=task_column,
        resource_name=resource_name,
    )
    session.execute(
        delete(model).where(
            model.id == asset_id,  # type: ignore[attr-defined]
            model.project_id == scope.project_id,  # type: ignore[attr-defined]
            model.status == CollectionAssetStatus.DRAFT.value,  # type: ignore[attr-defined]
        )
    )
    session.flush()


def _assert_no_task_reference(
    session: Session,
    scope: ProjectScope,
    *,
    asset_id: UUID,
    task_column: object,
    resource_name: str,
) -> None:
    """删除前查引用：被任务引用的素材拒绝删除（可读错误）。

    数据库侧有四条 ``(project_id, xxx_id)`` 复合外键 + ``ON DELETE RESTRICT``，
    它确实会拒绝——但 ``IntegrityError`` 原文是方言相关的 SQL 片段，客户端
    只能拿到笼统的 409 ``conflict``。这里先给出「被任务引用」的明确语义，
    让调用方知道该先解绑任务而不是重试。

    只查存在性，不把任务数量或 ID 读出来：删除方不需要知道谁在引用它，
    泄漏任务 ID 反而扩大信息面。
    """
    referenced = session.scalar(
        select(
            exists().where(
                CollectionTask.project_id == scope.project_id,
                task_column == asset_id,  # type: ignore[arg-type]
            )
        )
    )
    if referenced:
        raise ResourceImmutableError(f"{resource_name}已被采集任务引用，不能删除")


__all__ = ["delete_asset_draft", "save_asset_update"]
