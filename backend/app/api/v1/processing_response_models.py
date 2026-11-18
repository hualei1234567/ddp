"""处理运行 HTTP 响应模型。

以运行时真实返回为准，并与 ``packages/contracts/fragments/schemas.processing.yaml``
对齐：

- ``allowed_actions`` 是服务端根据状态机算出的权威值，客户端只按它渲染按钮；
  其中永远不会出现 ``system-*`` / ``worker-*``；
- ``version`` 对应数据库 ``version`` 列，用于 ``If-Match``；
- ``steps`` 只在详情响应内联（有界）；列表响应为 ``null``，避免把每一步
  都塞进分页列表；完整历史走 ``GET .../steps``；
- 可空字段一律 ``| None`` 且给默认值，避免导出成"运行时更严"的必填差异。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.processing.models import ProcessingRun
from app.domain.processing.steps import ProcessingStep


class ProcessingRunStepOut(BaseModel):
    step_id: UUID
    run_id: UUID
    stage: str
    attempt: int
    status: str
    log_summary: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, step: ProcessingStep) -> ProcessingRunStepOut:
        return cls(
            step_id=step.step_id,
            run_id=step.run_id,
            stage=step.stage,
            attempt=step.attempt,
            status=step.status,
            log_summary=step.log_summary,
            error_code=step.error_code,
            error_summary=step.error_summary,
            started_at=step.started_at,
            finished_at=step.finished_at,
            created_at=step.created_at,
            updated_at=step.updated_at,
        )


class ProcessingRunOut(BaseModel):
    run_id: UUID
    project_id: UUID
    pipeline_version: str
    input_refs: list[str]
    output_refs: list[str]
    status: str
    stage: str | None = None
    attempt: int
    retry_count: int
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    allowed_actions: list[str]
    version: int
    created_at: datetime
    updated_at: datetime
    steps: list[ProcessingRunStepOut] | None = None

    @classmethod
    def from_domain(
        cls,
        run: ProcessingRun,
        *,
        steps: list[ProcessingStep] | None = None,
    ) -> ProcessingRunOut:
        return cls(
            run_id=run.run_id,
            project_id=run.project_id,
            pipeline_version=run.pipeline_version,
            input_refs=list(run.input_refs),
            output_refs=list(run.output_refs),
            status=run.status,
            stage=run.stage,
            attempt=run.attempt,
            retry_count=run.retry_count,
            error_code=run.error_code,
            error_summary=run.error_summary,
            started_at=run.started_at,
            finished_at=run.finished_at,
            allowed_actions=sorted(run.allowed_actions),
            version=run.row_version,
            created_at=run.created_at,
            updated_at=run.updated_at,
            steps=None
            if steps is None
            else [ProcessingRunStepOut.from_domain(step) for step in steps],
        )


__all__ = ["ProcessingRunOut", "ProcessingRunStepOut"]
