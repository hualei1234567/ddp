"""标注任务项目级逾期汇总路由（N6-002 收尾，只读）。

单独成文件的原因：``annotation_work.py`` 已接近 500 行评审线，而汇总卡片与
任务状态机无关。端点只读、不触发任何状态变更；``project_id`` 由服务端作用域
强制，不信任客户端声明。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.annotation_common import ReadScope
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_overdue_models import AnnotationOverdueSummaryOut

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-tasks"])


@router.get(
    "/annotation-overdue-summary",
    response_model=AnnotationOverdueSummaryOut,
)
def get_overdue_summary(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationOverdueSummaryOut:
    """本项目逾期任务汇总：总数、优先级/状态分布、最早期逾期时间与 SLA 分桶。"""
    return AnnotationOverdueSummaryOut.from_domain(use_cases.overdue_summary.summary(scope))


__all__ = ["router"]
