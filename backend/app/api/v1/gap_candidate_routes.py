"""数据缺口候选 HTTP 路由（N7-005）。

- ``GET /data-gap-candidates``：只读候选视图，不创建任何记录；
- ``POST /data-gap-candidates/{candidate_key}/data-requirements``：显式人工动作，
  从当前候选创建 **DRAFT** 需求，复用既有需求创建用例与幂等语义。

没有"一键把全部候选转需求"的批量入口，也没有自动审批：候选只是事实线索，
是否转需求由人逐条确认。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from app.api.v1.modelops_dependencies import (
    IdempotencyKey,
    ModelOpsServicesDep,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.modelops_response_models import (
    DataGapCandidateSummaryOut,
    DataRequirementOut,
)
from app.api.v1.modelops_schemas import GapCandidateConvertRequest
from app.application.modelops.commands import NewRequirementFromCandidate
from app.domain.modelops.gap_candidates import MAX_CANDIDATE_LIMIT

router = APIRouter(
    prefix="/projects/{project_id}/data-gap-candidates",
    tags=["data-gap-candidates"],
)


@router.get("", response_model=DataGapCandidateSummaryOut)
def list_gap_candidates(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    limit: Annotated[int, Query(ge=1, le=MAX_CANDIDATE_LIMIT)] = 50,
) -> DataGapCandidateSummaryOut:
    return DataGapCandidateSummaryOut.from_domain(services.candidates.list(scope, limit=limit))


@router.post(
    "/{candidate_key}/data-requirements",
    response_model=DataRequirementOut,
    status_code=201,
)
def convert_gap_candidate(
    candidate_key: Annotated[str, Path(min_length=1, max_length=64)],
    payload: GapCandidateConvertRequest,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> DataRequirementOut:
    requirement = services.candidates.convert(
        scope,
        candidate_key,
        NewRequirementFromCandidate(
            code=payload.code,
            title=payload.title,
            owner_id=payload.owner_id,
            target_episode_count=payload.target_episode_count,
            description=payload.description,
            quality_thresholds=payload.quality_thresholds,
            priority=payload.priority,
            due_at=payload.due_at,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DataRequirementOut.from_domain(requirement)


__all__ = ["router"]
