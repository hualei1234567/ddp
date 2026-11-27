"""数据集版本对比路由（阶段 5 / 治理闭环）。

权限：``DatasetVersionReadScope``（能读版本的人才能看对比）。
契约：路径只进 fragments，**不 bump openapi version**。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.api.v1.training_dependencies import DatasetVersionReadScope
from app.application.datasets.version_compare import (
    DEFAULT_ITEM_COMPARE_LIMIT,
    compare_versions,
)
from app.domain.identity.models import ProjectScope

router = APIRouter(
    tags=["dataset-versions"],
    prefix="/projects/{project_id}/datasets/{dataset_id}/versions",
)

Scope = Annotated[ProjectScope, Depends(DatasetVersionReadScope)]


class StatDiffOut(BaseModel):
    """单个统计维度的变化（基线 → 候选）。"""

    baseline: int
    candidate: int
    changed: bool = Field(description="是否发生变化")


class VersionComparisonOut(BaseModel):
    """版本对比结果。"""

    dataset_id: str
    baseline_version: int
    candidate_version: int
    items_added: int = Field(description="候选新增的条目数")
    items_removed: int = Field(description="基线被移除的条目数")
    items_common: int = Field(description="两版共有的条目数")
    item_comparison_truncated: bool = Field(
        description="条目对比是否因上限截断（为 true 时计数是部分值）"
    )
    added_samples: list[dict[str, object]] = Field(description="新增条目样例（≤5 条）")
    removed_samples: list[dict[str, object]] = Field(description="移除条目样例（≤5 条）")
    stats_diff: dict[str, StatDiffOut] = Field(description="统计维度变化")
    policy_changes: dict[str, str] = Field(
        description="策略指纹变化（格式：旧 → 新）；未变的策略不出现"
    )


@router.get(
    "/{baseline_version_id}/compare/{candidate_version_id}",
    response_model=VersionComparisonOut,
    summary="对比同一数据集的两个版本",
)
def compare_dataset_versions(
    scope: Scope,
    dataset_id: UUID,
    baseline_version_id: UUID,
    candidate_version_id: UUID,
    session: DbSession,
    limit: int = Query(
        default=DEFAULT_ITEM_COMPARE_LIMIT,
        ge=1,
        le=100_000,
        description="条目对比上限；超限返回截断标记而非静默截断",
    ),
) -> VersionComparisonOut:
    result = compare_versions(
        session,
        scope,
        dataset_id=dataset_id,
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        limit=limit,
    )
    return VersionComparisonOut(
        dataset_id=str(result.dataset_id),
        baseline_version=result.baseline_version,
        candidate_version=result.candidate_version,
        items_added=result.items_added,
        items_removed=result.items_removed,
        items_common=result.items_common,
        item_comparison_truncated=result.item_comparison_truncated,
        added_samples=list(result.added_samples),
        removed_samples=list(result.removed_samples),
        stats_diff={
            name: StatDiffOut(baseline=base, candidate=cand, changed=base != cand)
            for name, (base, cand) in result.stats_diff.items()
        },
        policy_changes=result.policy_changes,
    )
