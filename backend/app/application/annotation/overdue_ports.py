"""标注逾期提醒的应用层端口（N6-005）。

单独成文件的原因：``application/annotation/ports.py`` 已超过 800 行并登记在
TD-01（巨型文件拆分）。新增协议不得继续把它撑大，因此逾期提醒相关的两个
只读端口独立声明；``AnnotationTaskRepository`` 通过继承
:class:`OverdueTaskDirectory` 保持既有契约，所有调用点与实现类无需改动。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.annotation.overdue_summary import OverdueGroup
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope


class OverdueTaskDirectory(Protocol):
    """跨项目读取逾期任务的最小能力（只读）。

    逾期提醒巡检需要一次看到所有项目，因此与 ``count_overdue`` 一样不做项目
    过滤；调用方在写提醒前必须按任务自身的 ``project_id`` 构造
    :class:`ProjectScope`，由仓储与审计共同兜底隔离。
    """

    def list_overdue_candidates(
        self,
        now: datetime,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AnnotationTask]:
        """**跨项目**列出逾期任务（按 id 键集游标）；只读。"""
        ...


class OverdueAlertEventRepository(Protocol):
    """逾期提醒的去重读模型：只查询已落库的窗口键，不写事件。"""

    def recorded_overdue_windows(
        self,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> set[str]: ...


class ProjectOverdueSummaryReader(Protocol):
    """项目级逾期汇总的只读聚合能力（N6-002 收尾）。

    与跨项目的 :class:`OverdueTaskDirectory` 不同，本端口**必须**接收
    :class:`ProjectScope`：汇总给项目成员看，服务端强制项目隔离，聚合 SQL
    只允许命中调用方所在项目的任务。
    """

    def overdue_groups(self, scope: ProjectScope, now: datetime) -> list[OverdueGroup]:
        """按「优先级 × 状态 × 分桶」聚合逾期任务；只读，不写库。"""
        ...


__all__ = [
    "OverdueAlertEventRepository",
    "OverdueTaskDirectory",
    "ProjectOverdueSummaryReader",
]
