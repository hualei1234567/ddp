"""处理运行 HTTP 路由（N4-001）。

路由只做协议适配（路径、请求体、``If-Match``、``ETag``）；状态机、
阶段顺序与乐观锁都在领域/应用层。

- 客户端只能看到 ``allowed_actions``，其中永远不会出现 ``system-*`` /
  ``worker-*``；
- ``POST .../actions/{cancel}`` 是唯一暴露的客户端动作；``action`` 由应用层
  按白名单再校验一次，未知动作不会落库；
- ``project_id`` 只用于定位路径，授权与数据隔离由服务端 ``ProjectScope`` 强制。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import request_id
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.processing_dependencies import (
    ExpectedVersion,
    ProcessingServicesDep,
    ReadScope,
    WriteScope,
)
from app.api.v1.processing_response_models import ProcessingRunOut, ProcessingRunStepOut
from app.api.v1.processing_schemas import ProcessingRunCreate
from app.api.v1.schemas import PageOut
from app.application.processing.commands import NewProcessingRun
from app.domain.processing.enums import ProcessingRunStatus, ProcessingStage

router = APIRouter(prefix="/projects/{project_id}/processing-runs", tags=["processing"])


@router.get("", response_model=PageOut[ProcessingRunOut])
def list_processing_runs(
    scope: ReadScope,
    services: ProcessingServicesDep,
    status: ProcessingRunStatus | None = None,
    stage: ProcessingStage | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[ProcessingRunOut]:
    items = services.runs.list(
        scope,
        status=status.value if status is not None else None,
        stage=stage.value if stage is not None else None,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.run_id)
    return PageOut(
        items=[ProcessingRunOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=ProcessingRunOut, status_code=201)
def create_processing_run(
    payload: ProcessingRunCreate,
    request: Request,
    response: Response,
    scope: WriteScope,
    services: ProcessingServicesDep,
) -> ProcessingRunOut:
    """创建运行，初始状态固定为 ``CREATED``。

    创建只落一行运行 + 一条投递类 outbox 事件（``processing.run.requested``），
    真正的八阶段执行由 ``app.workers.processing`` 异步完成；HTTP 线程不会
    因为一次大文件处理而阻塞。
    """
    run = services.runs.create(
        scope,
        NewProcessingRun(
            pipeline_version=payload.pipeline_version,
            input_refs=tuple(payload.input_refs),
        ),
        # 透传请求 ID，Worker 才能把异步执行链路与发起请求对齐。
        request_id=request_id(request),
    )
    response.headers["ETag"] = f'"{run.row_version}"'
    return ProcessingRunOut.from_domain(run)


@router.get("/{run_id}", response_model=ProcessingRunOut)
def get_processing_run(
    run_id: UUID,
    response: Response,
    scope: ReadScope,
    services: ProcessingServicesDep,
) -> ProcessingRunOut:
    run = services.runs.get(scope, run_id)
    steps = services.runs.detail_steps(scope, run_id)
    response.headers["ETag"] = f'"{run.row_version}"'
    return ProcessingRunOut.from_domain(run, steps=steps)


@router.get("/{run_id}/steps", response_model=PageOut[ProcessingRunStepOut])
def list_processing_run_steps(
    run_id: UUID,
    scope: ReadScope,
    services: ProcessingServicesDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[ProcessingRunStepOut]:
    """按游标分页列出步骤；运行不存在或跨项目一律 404（不泄露存在性）。"""
    items = services.runs.list_steps(
        scope,
        run_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.step_id)
    return PageOut(
        items=[ProcessingRunStepOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/{run_id}/actions/{action}", response_model=ProcessingRunOut)
def act_on_processing_run(
    run_id: UUID,
    action: str,
    response: Response,
    scope: WriteScope,
    services: ProcessingServicesDep,
    expected_version: ExpectedVersion,
) -> ProcessingRunOut:
    """客户端动作：当前只暴露 ``cancel``，且必须带 ``If-Match: "<version>"``。"""
    run = services.runs.act(
        scope,
        run_id,
        action,
        expected_version=expected_version,
        now=None,
    )
    response.headers["ETag"] = f'"{run.row_version}"'
    return ProcessingRunOut.from_domain(run)


__all__ = ["router"]
