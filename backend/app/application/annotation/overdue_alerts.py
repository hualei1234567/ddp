"""标注任务逾期提醒（P2，DDP-V21-N6-005）。

**做什么**

巡检发现任务进入逾期后，写一条 ``annotation.task.overdue`` 留痕事件。
业务写入与审计/outbox 同事务；去重键是
:func:`app.domain.annotation.overdue.overdue_window` 定义的「任务 + ``due_at``」窗口。

**为什么是留痕类，不接外部通知**

当前仓库没有任何站内信/邮件/IM 消费者。把它标成投递类（``dispatch=True``）
只会得到一条永不消费的 ``PENDING`` 消息，正是 R-13 要消灭的形态。因此事件
按留痕类写入（``RECORDED``，不进重试队列），供审计查询与后续消费者使用。

后续接入站内/邮件通知时，应新增一个独立消费者读取本事件，并把该事件改登记为
投递类（``scripts/check_outbox_events.py`` 的 ``DISPATCH_EVENTS`` 与
``docs/architecture/outbox事件语义界定-v1.0.md`` 同步登记）；本模块无需改判口径。

**去重与失败处理**

- 同一任务同一 ``due_at`` 窗口只发一次：写前按项目查询已落库窗口键；
- 事件 id 由窗口确定性派生（``uuid5``），并发重复写入会撞主键而不是产生第二条；
- 任务进入终态或不再逾期时，领域判定直接返回 ``None``，不会补发；
- 单任务失败回滚本任务并继续，下一轮巡检重试；批量上限由调用方传入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid5

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.application.annotation.audit import record_change
from app.application.annotation.ports import (
    AnnotationTaskRepository,
    AuditRepository,
    OutboxRepository,
    OverdueAlertEventRepository,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.overdue import build_overdue_alert
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope

logger = logging.getLogger("ddp.application.annotation.overdue_alerts")

#: 逾期提醒事件类型（留痕类）。消费计划见模块文档。
OVERDUE_ALERT_EVENT = "annotation.task.overdue"
_RESOURCE_TYPE = "ANNOTATION_TASK"
# 事件 id 的确定性命名空间：同一窗口永远得到同一个 id，作为并发去重的最后一道闸。
_ALERT_NAMESPACE = UUID("2d7c9a51-6b3e-4c8f-9a10-5e4d3c2b1a09")


class ProjectOwnerResolver(Protocol):
    """系统动作需要的可审计执行人（项目负责人）。"""

    def first_owner_id(self, project_id: UUID) -> UUID | None: ...


class AlertOutcome(StrEnum):
    EMITTED = "EMITTED"
    ALREADY_RECORDED = "ALREADY_RECORDED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class OverdueAlertSweepResult:
    scanned: int
    emitted: int
    already_recorded: int
    next_after_id: UUID | None


def overdue_alert_event_id(window: str) -> UUID:
    """由去重窗口确定性派生事件 id；同一窗口重复写入会撞主键。"""
    return uuid5(_ALERT_NAMESPACE, f"{OVERDUE_ALERT_EVENT}:{window}")


class AnnotationOverdueAlertSweep:
    """巡检用例：为进入逾期窗口的任务写一次提醒留痕。"""

    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        alert_events: OverdueAlertEventRepository,
        owners: ProjectOwnerResolver,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: object,
    ) -> None:
        self._tasks = tasks
        self._alert_events = alert_events
        self._owners = owners
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def run_once(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
        after_id: UUID | None = None,
    ) -> OverdueAlertSweepResult:
        """扫一批逾期任务并提醒；``limit`` 是单轮事件上限，防止事件风暴。"""
        if limit < 1:
            raise DomainValidationError("limit 必须为正数")
        moment = now or datetime.now(UTC)
        # 多取一条判断是否还有下一页；游标让提醒过的任务不会永久占满批次。
        candidates = self._tasks.list_overdue_candidates(
            moment,
            after_id=after_id,
            limit=limit + 1,
        )
        has_more = len(candidates) > limit
        batch = candidates[:limit]
        next_after_id = batch[-1].task_id if has_more and batch else None
        emitted = 0
        already = 0
        for task in batch:
            try:
                outcome = self._alert(task, now=moment)
            except SQLAlchemyError:
                # 单个任务失败：回滚本任务，其余任务继续；下一轮重试。
                self._uow.rollback()  # type: ignore[attr-defined]
                logger.exception(
                    "标注任务逾期提醒失败，下一轮重试",
                    extra={
                        "task_id": str(task.task_id),
                        "project_id": str(task.project_id),
                    },
                )
                continue
            if outcome is AlertOutcome.EMITTED:
                emitted += 1
            elif outcome is AlertOutcome.ALREADY_RECORDED:
                already += 1
        result = OverdueAlertSweepResult(
            scanned=len(batch),
            emitted=emitted,
            already_recorded=already,
            next_after_id=next_after_id,
        )
        logger.info(
            "标注任务逾期提醒巡检完成",
            extra={
                "scanned": result.scanned,
                "emitted": result.emitted,
                "already_recorded": result.already_recorded,
            },
        )
        return result

    def _alert(self, task: AnnotationTask, *, now: datetime) -> AlertOutcome:
        alert = build_overdue_alert(task, now)
        if alert is None:
            return AlertOutcome.SKIPPED
        owner_id = self._owners.first_owner_id(alert.project_id)
        if owner_id is None:
            # 没有负责人就没有可审计的执行人；跳过而不是伪造一个系统账号。
            logger.warning(
                "标注任务逾期提醒跳过：项目没有负责人",
                extra={
                    "task_id": str(alert.task_id),
                    "project_id": str(alert.project_id),
                },
            )
            return AlertOutcome.SKIPPED
        scope = ProjectScope(alert.project_id, actor_id=owner_id)
        if alert.window in self._alert_events.recorded_overdue_windows(scope, [alert.task_id]):
            return AlertOutcome.ALREADY_RECORDED
        payload = {
            "task_id": str(alert.task_id),
            "task_code": alert.task_code,
            "project_id": str(alert.project_id),
            "status": alert.status,
            "due_at": alert.due_at.isoformat(),
            "overdue_seconds": alert.overdue_seconds,
            "overdue_window": alert.window,
        }
        try:
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE_TYPE,
                resource_id=alert.task_id,
                action="overdue",
                from_status=alert.status,
                to_status=alert.status,
                reason=None,
                request_id=None,
                details=dict(payload),
                event_type=OVERDUE_ALERT_EVENT,
                event_payload=payload,
                occurred_at=now,
                event_id=overdue_alert_event_id(alert.window),
            )
            self._uow.commit()  # type: ignore[attr-defined]
        except IntegrityError:
            # 确定性事件 id 撞主键：另一个并发巡检已为同一窗口发过提醒。
            # 只有回滚后确实查到该窗口才按"已记录"处理；其他完整性错误重新抛，
            # 由巡检统一记日志并在下一轮重试，不能静默吞掉。
            self._uow.rollback()  # type: ignore[attr-defined]
            if alert.window in self._alert_events.recorded_overdue_windows(scope, [alert.task_id]):
                logger.info(
                    "标注任务逾期提醒已存在（并发或重复扫描），跳过",
                    extra={"task_id": str(alert.task_id), "window": alert.window},
                )
                return AlertOutcome.ALREADY_RECORDED
            raise
        return AlertOutcome.EMITTED
