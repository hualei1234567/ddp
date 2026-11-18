"""PATCH 变更留痕的单一权威实现（G-11）。

devices / sensors / robots 三个聚合的 PATCH 都要回答同一个问题："这次到底改
了什么？" 规则若各写一份，很快就会出现三套不一致的判定——例如把请求里的
``"CAMERA_RIG"`` 与领域层的 ``DeviceType.CAMERA_RIG`` 当成两个值，于是每次
都记一条"改了 device_type"的假变更。因此判定与写入都只在这里做。

两条语义：

1. **只记真正变化的字段**：比较对象是领域层 ``__post_init__`` 规范化之后的
   实体值，不是原始请求值。客户端"点了保存但什么都没改"的 PATCH 仍会归档
   （版本号语义不变），但不写审计行——否则 ``audit_logs`` 会被空变更淹没，
   真正改了什么反而难找。
2. **与业务写入同事务**：审计与 outbox 由 :func:`record_change` 写入，
   失败即向上抛，由用例层的事务边界回滚。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from uuid import UUID

from app.application.audit import AuditRepository, OutboxRepository, record_change
from app.domain.identity.models import ProjectScope


def changed_fields(
    current: object,
    candidate: object,
    requested: Mapping[str, object],
) -> list[str]:
    """返回 ``requested`` 中取值确实发生变化的字段名（按字母排序）。

    ``candidate`` 必须是已完成领域规范化的实体（``__post_init__`` 之后）：
    枚举、文本裁剪、JSON 对象拷贝都在那里收敛，用原始请求值比较会误报。
    只比较请求涉及的字段，未出现的字段一律视为"本次不改"。
    """
    return sorted(
        name
        for name in requested
        if _normalized(getattr(current, name)) != _normalized(getattr(candidate, name))
    )


def record_patch(
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    resource_type: str,
    resource_id: UUID,
    id_field: str,
    current: object,
    candidate: object,
    changes: Mapping[str, object],
    event_type: str,
    request_id: str | None,
    occurred_at: datetime,
) -> list[str]:
    """按 PATCH 的真实变更写一条审计与 outbox；无变化时不写。

    ``id_field`` 是事件负载里标识资源的键名（``device_id`` / ``sensor_id`` /
    ``robot_id``）：事件名与负载键是已落库的可观测契约，不能统一改成
    ``resource_id``，否则历史 consumers 与既有事件行会失配。

    返回真正变化的字段列表，便于调用方记录日志或测试断言。
    """
    changed = changed_fields(current, candidate, changes)
    if not changed:
        return []
    record_change(
        audit,
        outbox,
        scope,
        resource_type=resource_type,
        resource_id=resource_id,
        action="update",
        from_status=_status_text(current),
        to_status=_status_text(candidate),
        reason=None,
        request_id=request_id,
        details={"changed_fields": changed},
        event_type=event_type,
        event_payload={
            id_field: str(resource_id),
            "status": _status_text(candidate),
            "changed_fields": changed,
        },
        occurred_at=occurred_at,
    )
    return changed


def _status_text(entity: object) -> str | None:
    """取实体的状态值；PATCH 不改状态，但审计仍要能按状态过滤。"""
    status = getattr(entity, "status", None)
    if status is None:
        return None
    return status.value if hasattr(status, "value") else str(status)


def _normalized(value: object) -> object:
    """把枚举与容器收敛成可直接比较的纯值。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalized(item) for item in value]
    return value


__all__ = ["changed_fields", "record_patch"]
