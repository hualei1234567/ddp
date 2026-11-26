"""PRM 步级标注 HTTP 路由（V52-020/V52-032，契约 1.40.0）。

端点（PRM 标注创建/查询 + fork 预览/物化）：
- ``POST /projects/{id}/prm-trajectory-annotations``：登记一条轨迹的步级标注；
- ``GET  /projects/{id}/prm-trajectory-annotations``：项目内列表；
- ``GET  /projects/{id}/prm-trajectory-annotations/{annotation_id}``：按 ID 查询；
- ``POST /projects/{id}/prm-trajectory-annotations/{trajectory_id}/fork``：
  分叉重写。默认纯计算预览（只读）；``materialize=true`` 时同请求落库
  （需要写权限），响应带 ``materialized_annotation_id``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_project_context
from app.api.v1.annotation_common import ReadScope, WriteScope, request_id
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_prm_models import (
    PrmAnnotationCreate,
    PrmAnnotationOut,
    PrmForkPreviewOut,
    PrmForkRequest,
    StepDpoPairOut,
    StepMarkIn,
    StepMarkOut,
)
from app.api.v1.schemas import PageOut
from app.application.annotation.ports import PrmAnnotationView
from app.core.errors import ForbiddenError
from app.domain.annotation.prm_step import PrmAnnotation, StepMark
from app.domain.identity.models import ProjectAction, ProjectContext

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-prm"])

# 分叉端点默认只读（预览）；物化落库时按需升级为写权限——同一端点按请求体
# 条件授权，不能直接依赖 ``WriteScope``，否则只读用户连预览都会被拒。
ForkContext = Annotated[ProjectContext, Depends(get_project_context)]


def _assert_materialize_allowed(context: ProjectContext) -> None:
    """物化落库复用创建路径，权限口径必须与创建端点一致。"""
    if not context.can(ProjectAction.WRITE):
        raise ForbiddenError("分叉物化落库需要项目写权限")


def _to_domain_marks(marks: tuple[StepMarkIn, ...]) -> tuple[StepMark, ...]:
    return tuple(
        StepMark(
            mark.step_index,
            mark.label,
            comment=mark.comment,
            annotator=mark.annotator,
        )
        for mark in marks
    )


def _view_out(view: PrmAnnotationView) -> PrmAnnotationOut:
    return PrmAnnotationOut.from_domain(
        view.annotation,
        view.annotation_id,
        version=view.version,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.post(
    "/prm-trajectory-annotations",
    response_model=PrmAnnotationOut,
    status_code=201,
)
def create_prm_annotation(
    payload: PrmAnnotationCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> PrmAnnotationOut:
    annotation = PrmAnnotation(
        trajectory_id=payload.trajectory_id,
        step_actions=payload.step_actions,
        marks=_to_domain_marks(payload.marks),
        task_goal=payload.task_goal,
    )
    view = use_cases.prm_annotations.create(scope, annotation, request_id=request_id(request))
    return _view_out(view)


@router.get(
    "/prm-trajectory-annotations",
    response_model=PageOut[PrmAnnotationOut],
)
def list_prm_annotations(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    limit: int = 50,
) -> PageOut[PrmAnnotationOut]:
    views = use_cases.prm_annotations.list_for_project(scope, limit=limit)
    return PageOut(items=[_view_out(view) for view in views], next_cursor=None)


@router.get(
    "/prm-trajectory-annotations/{annotation_id}",
    response_model=PrmAnnotationOut,
)
def get_prm_annotation(
    annotation_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> PrmAnnotationOut:
    return _view_out(use_cases.prm_annotations.get(scope, annotation_id))


@router.post(
    "/prm-trajectory-annotations/{trajectory_id}/fork",
    response_model=PrmForkPreviewOut,
)
def fork_prm_trajectory(
    trajectory_id: str,
    payload: PrmForkRequest,
    request: Request,
    scope: ReadScope,
    context: ForkContext,
    use_cases: AnnotationUseCasesDep,
) -> PrmForkPreviewOut:
    """分叉重写：默认只读预览；``materialize=true`` 时同请求物化落库。"""
    if payload.materialize:
        _assert_materialize_allowed(context)
    result = use_cases.prm_annotations.fork(
        scope,
        trajectory_id,
        at_index=payload.at_index,
        new_actions=payload.new_actions,
        materialize=payload.materialize,
        request_id=request_id(request),
    )
    materialized_id = result.materialized.annotation_id if result.materialized is not None else None
    return PrmForkPreviewOut(
        source_trajectory_id=result.forked.source_trajectory_id,
        fork_point_index=result.forked.fork_point_index,
        forked_trajectory_id_preview=result.forked_trajectory_id,
        forked_steps=list(result.forked.step_actions),
        shared_prefix_steps=result.forked.prefix_step_count,
        forked_marks=[StepMarkOut.from_domain(mark) for mark in result.forked.marks],
        dpo_pair=StepDpoPairOut.from_domain(result.pair),
        materialized_annotation_id=materialized_id,
    )
