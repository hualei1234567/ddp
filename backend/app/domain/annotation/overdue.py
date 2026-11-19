"""标注任务逾期提醒的领域规则（纯函数）。

逾期本身是派生事实，判定口径只有一处：:func:`app.domain.annotation.task.is_overdue`。
本模块不重新定义"什么算逾期"，只回答两件事：

1. 某个任务在 ``now`` 时刻应该产生什么提醒（:func:`build_overdue_alert`）；
2. 哪些提醒还没有发过（:func:`select_overdue_alerts`）。

**去重窗口**

提醒的去重单元是「任务 + 冻结的 ``due_at``」。``due_at`` 是 SLA 窗口的锚点：

- 同一任务、同一个 ``due_at`` 只提醒一次，巡检每轮重复看到它也不会重复发；
- 修改截止时间（延期或重新排期）等于进入**新的窗口**，到期后会再次提醒；
- 任务进入终态（完成 / 取消 / 归档）或 ``due_at`` 被移除后不再提醒，
  历史窗口键仍保留，任务若再次逾期且 ``due_at`` 未变也不会重复发。

窗口键统一用 UTC ISO 表示，避免同一时刻因时区偏移不同被当成两个窗口。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.task import AnnotationTask, is_overdue


@dataclass(frozen=True, slots=True)
class OverdueAlert:
    """一个待留痕的逾期提醒；``window`` 是该提醒的去重键。"""

    task_id: UUID
    project_id: UUID
    task_code: str
    status: str
    due_at: datetime
    overdue_seconds: int
    window: str


def overdue_window(task: AnnotationTask) -> str | None:
    """逾期窗口键：``<task_id>:<due_at UTC ISO>``；无截止时间返回 ``None``。"""
    if task.due_at is None:
        return None
    due_at = task.due_at.astimezone(UTC)
    return f"{task.task_id}:{due_at.isoformat()}"


def build_overdue_alert(task: AnnotationTask, now: datetime) -> OverdueAlert | None:
    """构造 ``now`` 时刻的逾期提醒；未逾期（含恰好到期、终态、无截止）返回 ``None``。"""
    if not is_overdue(task, now):
        return None
    due_at = task.due_at
    window = overdue_window(task)
    if due_at is None or window is None:  # pragma: no cover - is_overdue 已保证
        return None
    status = getattr(task.status, "value", task.status)
    return OverdueAlert(
        task_id=task.task_id,
        project_id=task.project_id,
        task_code=task.code,
        status=str(status),
        due_at=due_at.astimezone(UTC),
        overdue_seconds=int((now - due_at).total_seconds()),
        window=window,
    )


def select_overdue_alerts(
    tasks: Iterable[AnnotationTask],
    now: datetime,
    *,
    emitted_windows: Collection[str],
) -> list[OverdueAlert]:
    """从候选任务中挑出尚未提醒、且本批内不重复的逾期窗口。

    ``emitted_windows`` 是已经落库的窗口键（跨项目按项目隔离查询后汇总）；
    返回顺序与输入顺序一致，便于测试与日志稳定复现。
    """
    seen = set(emitted_windows)
    alerts: list[OverdueAlert] = []
    for task in tasks:
        alert = build_overdue_alert(task, now)
        if alert is None or alert.window in seen:
            continue
        seen.add(alert.window)
        alerts.append(alert)
    return alerts
