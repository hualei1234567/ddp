"""统一质量引擎 HTTP 路由（N4-004 / N4-008）。

- ``POST`` 对单个 Episode 或导入任务同步执行规则；运行只追加，重复调用会
  产生多条历史记录，不覆盖旧结论；
- ``GET`` 列表按对象（``subject_type`` + ``subject_id``）列举历史运行，
  创建时间/ID 倒序游标分页；对象不存在时返回空页而不是 404；
- ``GET`` 详情返回六类聚合；结论明细走子资源游标分页；
- ``project_id`` 只用于定位路径，授权与隔离由服务端 ``ProjectScope`` 强制。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.quality_dependencies import (
    QualityServicesDep,
    ReadScope,
    WriteScope,
)
from app.api.v1.quality_models import (
    QualityFindingOut,
    QualityRunDiffOut,
    QualityRunOut,
    QueuedQualityRunOut,
)
from app.api.v1.quality_schemas import QualityRunBatchCreate, QualityRunCreate
from app.api.v1.schemas import PageOut
from app.application.quality.commands import NewQualityRun
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySubjectType,
)

router = APIRouter(prefix="/projects/{project_id}/quality-runs", tags=["quality"])


@router.get("", response_model=PageOut[QualityRunOut])
def list_quality_runs(
    scope: ReadScope,
    services: QualityServicesDep,
    subject_type: QualitySubjectType,
    subject_id: UUID,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[QualityRunOut]:
    """按对象列举历史质量运行（创建时间/ID 倒序）。

    ``subject_type`` 与 ``subject_id`` 必填：本接口是"某个对象的历史运行"，
    不支持跨对象全量扫描。对象不存在或跨项目返回空页而不是 404——列表
    过滤没有可定位的单条资源，也不应通过 404/200 差异泄漏对象存在性。
    """
    items = services.runs.list_runs(
        scope,
        subject_type=subject_type,
        subject_id=subject_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.run_id)
    return PageOut(
        items=[QualityRunOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=QualityRunOut, status_code=201)
def create_quality_run(
    payload: QualityRunCreate,
    scope: WriteScope,
    services: QualityServicesDep,
) -> QualityRunOut:
    """对单个 Episode 或导入任务执行一次质量检查。

    对象不存在或跨项目返回 404；未知类别/对象类型由请求模型拒绝并映射为
    ``validation_failed``。规则集版本在运行时固化到响应与审计记录。
    """
    command = NewQualityRun(
        subject_type=QualitySubjectType(payload.subject_type),
        subject_id=payload.subject_id,
        categories=tuple(payload.categories) if payload.categories else None,
    )
    run = services.runs.run(scope, command)
    return QualityRunOut.from_domain(run)


@router.post(
    "/batch",
    response_model=list[QueuedQualityRunOut],
    status_code=201,
)
def rerun_quality_runs_batch(
    payload: QualityRunBatchCreate,
    scope: WriteScope,
    services: QualityServicesDep,
) -> list[QueuedQualityRunOut]:
    """批量重跑：为一组 Episode 各排一次质检（``QUEUED``）。

    与 ``POST /quality-runs`` 的区别只有执行时机：本端点只入队，由质检
    Worker 领取执行；单次端点仍在请求线程里同步算完。空列表、重复对象、
    超过上限由用例拒绝并映射为 ``validation_failed``。
    """
    runs = services.runs.rerun_batch(scope, payload.subject_ids)
    return [QueuedQualityRunOut.from_domain(run) for run in runs]


@router.get(
    "/{baseline_run_id}/diff/{candidate_run_id}",
    response_model=QualityRunDiffOut,
)
def compare_quality_runs(
    baseline_run_id: UUID,
    candidate_run_id: UUID,
    scope: ReadScope,
    services: QualityServicesDep,
) -> QualityRunDiffOut:
    """比对两次运行的结论（差异报告）。

    两次运行必须检查同一对象；任一运行未产出结论时拒绝（没有可信结论就
    没有可比性）。运行不存在或跨项目返回 404。
    """
    diff = services.diff.compare(scope, baseline_run_id, candidate_run_id)
    return QualityRunDiffOut.from_domain(diff)


@router.get("/{run_id}", response_model=QualityRunOut)
def get_quality_run(
    run_id: UUID,
    scope: ReadScope,
    services: QualityServicesDep,
) -> QualityRunOut:
    run = services.runs.get(scope, run_id)
    return QualityRunOut.from_domain(run)


@router.get("/{run_id}/findings", response_model=PageOut[QualityFindingOut])
def list_quality_findings(
    run_id: UUID,
    scope: ReadScope,
    services: QualityServicesDep,
    category: QualityCategory | None = None,
    status: QualityFindingStatus | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[QualityFindingOut]:
    """按游标分页列出结论明细；运行不存在或跨项目返回 404。"""
    items = services.runs.list_findings(
        scope,
        run_id,
        category=category,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.finding_id)
    return PageOut(
        items=[QualityFindingOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


__all__ = ["router"]
