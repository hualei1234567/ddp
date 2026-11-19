"""标注任务项目级逾期汇总用例（N6-002 收尾，只读）。

**做什么**

把仓储按「优先级 × 状态 × 逾期分桶」的聚合结果折叠成
:class:`app.domain.annotation.overdue_summary.OverdueSummary`，供全局逾期卡片
展示。聚合口径（什么算逾期、如何分桶）全部在领域层，本模块只做编排：

- 强制 ``ProjectScope``：仓储 SQL 以 ``scope.project_id`` 过滤，不信任客户端；
- 只读：不写库、不推进版本、不触发任何状态变更；
- 单一"现在"：同一次请求内所有分桶基于同一个 ``now``，避免边界时刻前后不一致。

**为什么单独成文件**

``application/annotation/tasks.py`` 已超过 500 行评审线；逾期汇总与任务状态机
无关，独立成模块与 ``overdue_alerts.py`` / ``overdue_ports.py`` 的拆分口径一致。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.annotation.overdue_ports import ProjectOverdueSummaryReader
from app.domain.annotation.overdue_summary import OverdueSummary, build_overdue_summary
from app.domain.annotation.validation import aware_datetime
from app.domain.identity.models import ProjectScope


class AnnotationOverdueSummaryUseCases:
    """项目级逾期汇总查询（只读）。"""

    def __init__(self, tasks: ProjectOverdueSummaryReader) -> None:
        self._tasks = tasks

    def summary(self, scope: ProjectScope, *, now: datetime | None = None) -> OverdueSummary:
        """返回本项目当前时刻的逾期汇总；无逾期任务时计数全为 0、时间为空。"""
        moment = aware_datetime(now, "now") if now is not None else datetime.now(UTC)
        groups = self._tasks.overdue_groups(scope, moment)
        return build_overdue_summary(scope.project_id, groups, moment)


__all__ = ["AnnotationOverdueSummaryUseCases"]
