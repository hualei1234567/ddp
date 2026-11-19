"""审计与事务性 outbox 编排的单一权威实现（写入 + 只读查询）。

审计与 outbox 必须与业务写入同事务：审计写入失败要让业务事务回滚
（fail-closed），提交后再由投递器发送事件。

为什么集中到一处：同一条"变更要留痕"的规则在 datasets / collection /
annotation / modelops 各写了一份（R-10），第五个域（devices）再写就变成 5 份。
各域文件 ``application/<domain>/audit.py`` 保留薄适配，``AuditRecord`` /
``OutboxEvent`` 类型由各域端口重新导出，调用点不变。

只读侧（``AuditLogEntry`` / ``list_audit_logs``）也放在这里：平台此前一直在写
``audit_logs``，却没有读入口，导致「数据可追溯」只有一半。读模型与写模型共用
同一个领域过滤值对象 :class:`AuditQuery`，避免 API 层再拼一套过滤语义。
查询本身只读、不写审计，也不提供任何修改或删除路径。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.domain.audit.errors import DomainValidationError
from app.domain.audit.models import AuditQuery
from app.domain.identity.models import ProjectScope

#: 单页上限与既有各域列表一致（契约 §4.1）。
MAX_QUERY_LIMIT = 100
#: 仓储多取一条用于判断是否还有下一页。
MAX_FETCH_LIMIT = MAX_QUERY_LIMIT + 1


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """一条不可变的变更留痕。"""

    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: Mapping[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """一条领域事件；与业务同事务落库。

    ``dispatch`` 区分两类语义（R-13 / T-1）：
    - ``True``（投递类）：有执行方需要据此做事。写入为 ``PENDING``，
      由 worker 抢占、重试、必要时进死信；没有消费者就是缺陷。
    - ``False``（留痕类）：只记录"发生了什么"。写入即 ``RECORDED``，
      不进重试队列，也不会被 worker 误捞。

    每个事件类型归入哪一类，以 ``docs/architecture/outbox事件语义界定-v1.0.md``
    的归类表为准；新增事件必须在 PR 中同步登记。
    """

    event_id: UUID
    project_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: Mapping[str, object]
    occurred_at: datetime
    dispatch: bool = False


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


def record_change(
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    resource_type: str,
    resource_id: UUID,
    action: str,
    from_status: str | None,
    to_status: str | None,
    reason: str | None,
    request_id: str | None,
    details: Mapping[str, object],
    event_type: str,
    event_payload: Mapping[str, object],
    occurred_at: datetime,
    result: str = "SUCCESS",
    actor_role: str | None = None,
    dispatch: bool = False,
    event_id: UUID | None = None,
) -> None:
    """同事务写审计与 outbox；任一失败都向上抛，由事务边界回滚。

    ``dispatch`` 默认 ``False``：绝大多数事件是留痕类。投递类必须显式传
    ``True``——默认值刻意选"不需要消费者"的那一侧，避免新增事件时
    无意间留下一条永远不会被消费的待投递消息。

    ``event_id`` 默认由 ``uuid4`` 生成；调用方只有在事件**天然按某个业务键
    去重**时才传入确定性 id（如逾期提醒按任务 + 截止时间窗口）。此时重复写入
    会撞主键，由调用方回滚并视为"已记录"，不产生第二条事件。
    """
    audit.record(
        scope,
        AuditRecord(
            audit_id=uuid4(),
            project_id=scope.project_id,
            actor_user_id=scope.actor_id,
            actor_role=actor_role,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            result=result,
            details=details,
            occurred_at=occurred_at,
        ),
    )
    outbox.enqueue(
        scope,
        OutboxEvent(
            event_id=event_id or uuid4(),
            project_id=scope.project_id,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            event_type=event_type,
            payload=event_payload,
            occurred_at=occurred_at,
            dispatch=dispatch,
        ),
    )


@dataclass(frozen=True, slots=True)
class AuditLogEntry:
    """一条已落库的审计留痕（只读投影）。

    字段与 ``audit_logs`` 一一对应，但不含 ORM 行对象：API 层不持有 Session，
    序列化也不会触发延迟加载。
    """

    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    correlation_id: str | None
    details: Mapping[str, object]
    occurred_at: datetime


class AuditLogQueryRepository(Protocol):
    """审计日志只读仓储。

    实现必须把 ``project_id`` 与全部过滤条件下推为 SQL，并在
    ``(occurred_at DESC, id DESC)`` 上做键集分页；``after_id`` 只承载
    游标记录的 ID，排序值由仓储用子查询取出。
    """

    def list(
        self,
        scope: ProjectScope,
        query: AuditQuery,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AuditLogEntry]: ...


def list_audit_logs(
    repository: AuditLogQueryRepository,
    scope: ProjectScope,
    query: AuditQuery,
    *,
    after_id: UUID | None = None,
    limit: int = 20,
) -> list[AuditLogEntry]:
    """按项目作用域查询审计日志，按发生时间倒序返回一页。

    ``limit`` 的边界规则只在这里校验一次；仓储只负责下推 SQL。
    跨项目 ID 不需要特殊分支：作用域已硬绑 ``project_id``，别家项目的
    记录根本不会被选中，调用方拿到的就是空结果（不泄露存在性）。
    """
    if limit < 1 or limit > MAX_FETCH_LIMIT:
        raise DomainValidationError(f"limit 必须在 1 到 {MAX_FETCH_LIMIT} 之间")
    return repository.list(
        scope,
        query,
        after_id=after_id,
        limit=limit,
    )
