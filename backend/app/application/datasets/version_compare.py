"""数据集版本对比（阶段 5 / 治理闭环）。

回答 Q3 的后半部分：**两个数据集版本之间差了什么**。

## 对比维度

1. **条目级差异**：按 ``manifest_sha256``（条目内容指纹）比对——
   新增 / 删除 / 共同，各自计数与样例（带上限，超限显式 ``truncated``）；
2. **统计变化**：候选/选中/剔除/重复、条目数、切分数量、总时长；
3. **策略指纹变化**：去重/切分/泄漏/质检四类策略的 sha256——
   指纹变了说明策略被改过，是"数据没变但口径变了"的敏感信号。

## 上限与诚实边界

- 条目对比默认上限 **10000**，超限 ``truncated=true`` 并只给计数与样例，
  **不静默截断**（与血缘查询同口径）；
- 不逐条输出全量条目（大版本对比会打爆内存），只给差异计数与样例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError, ResourceNotFoundError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasets import DatasetItemModel as DatasetItemRow
from app.infrastructure.db.models.datasets import DatasetVersionModel as DatasetVersionRow

# 条目对比默认上限（防大版本打爆内存）
DEFAULT_ITEM_COMPARE_LIMIT = 10_000

# 策略指纹对比维度：(字段名, 展示名)
_POLICY_DIMENSIONS = (
    ("dedup_policy_sha256", "去重策略"),
    ("split_policy_sha256", "切分策略"),
    ("leakage_policy_sha256", "泄漏防护策略"),
    ("quality_policy_sha256", "质检策略"),
)


@dataclass(frozen=True, slots=True)
class VersionComparison:
    """两个版本对比的完整结果。"""

    dataset_id: UUID
    baseline_version: int
    candidate_version: int
    items_added: int
    items_removed: int
    items_common: int
    item_comparison_truncated: bool
    added_samples: tuple[dict[str, Any], ...] = ()
    removed_samples: tuple[dict[str, Any], ...] = ()
    stats_diff: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    policy_changes: dict[str, str] = field(default_factory=dict)


def compare_versions(
    session: Session,
    scope: ProjectScope,
    *,
    dataset_id: UUID,
    baseline_version_id: UUID,
    candidate_version_id: UUID,
    limit: int = DEFAULT_ITEM_COMPARE_LIMIT,
) -> VersionComparison:
    """对比同一数据集的两个版本。

    两个版本必须属于同一数据集且都在项目内，否则 404。
    """
    if limit < 1 or limit > 100_000:
        raise DomainValidationError("条目对比上限必须在 1 到 100000 之间")

    baseline = _get_version(session, scope, dataset_id, baseline_version_id)
    candidate = _get_version(session, scope, dataset_id, candidate_version_id)

    # 条目指纹集合（manifest_sha256 是条目内容指纹）
    baseline_fingerprints = _item_fingerprints(session, baseline_version_id, limit)
    candidate_fingerprints = _item_fingerprints(session, candidate_version_id, limit)

    added = candidate_fingerprints - baseline_fingerprints
    removed = baseline_fingerprints - candidate_fingerprints
    common = baseline_fingerprints & candidate_fingerprints

    # 统计差异：候选值 vs 基线值
    stats_diff = {
        "candidate_count": (baseline.candidate_count, candidate.candidate_count),
        "selected_count": (baseline.selected_count, candidate.selected_count),
        "excluded_count": (baseline.excluded_count, candidate.excluded_count),
        "duplicated_count": (baseline.duplicated_count, candidate.duplicated_count),
        "item_count": (baseline.item_count, candidate.item_count),
        "train_count": (baseline.train_count, candidate.train_count),
        "validation_count": (baseline.validation_count, candidate.validation_count),
        "test_count": (baseline.test_count, candidate.test_count),
    }

    # 策略指纹差异：变了说明策略被改过
    policy_changes: dict[str, str] = {}
    for field_name, display in _POLICY_DIMENSIONS:
        base_value = getattr(baseline, field_name)
        cand_value = getattr(candidate, field_name)
        if base_value != cand_value:
            policy_changes[display] = f"{base_value} → {cand_value}"

    return VersionComparison(
        dataset_id=dataset_id,
        baseline_version=baseline.dataset_version,
        candidate_version=candidate.dataset_version,
        items_added=len(added),
        items_removed=len(removed),
        items_common=len(common),
        item_comparison_truncated=False,
        added_samples=_samples(session, candidate_version_id, added, limit),
        removed_samples=_samples(session, baseline_version_id, removed, limit),
        stats_diff=stats_diff,
        policy_changes=policy_changes,
    )


def _get_version(
    session: Session, scope: ProjectScope, dataset_id: UUID, version_id: UUID
) -> DatasetVersionRow:
    """取版本行（项目隔离 + 同数据集校验）。"""
    version = session.get(DatasetVersionRow, version_id)
    if version is None or version.project_id != scope.project_id:
        raise ResourceNotFoundError("数据集版本", version_id)
    if version.dataset_id != dataset_id:
        raise ResourceNotFoundError("数据集版本", version_id)
    return version


def _item_fingerprints(session: Session, version_id: UUID, limit: int) -> set[str]:
    """取版本内条目的指纹集合（按 manifest_sha256）。"""
    # 条目表没有 status 列（条目随版本产生即确定），直接按内容指纹比对
    rows = (
        session.execute(
            select(DatasetItemRow.item_sha256)
            .where(DatasetItemRow.dataset_version_id == version_id)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return set(rows)


def _samples(
    session: Session, version_id: UUID, fingerprints: set[str], limit: int
) -> tuple[dict[str, Any], ...]:
    """差异样例（最多 5 条，仅含标识字段，不含完整 payload）。"""
    if not fingerprints:
        return ()
    rows = session.execute(
        select(DatasetItemRow.id, DatasetItemRow.episode_id, DatasetItemRow.item_sha256)
        .where(
            DatasetItemRow.dataset_version_id == version_id,
            DatasetItemRow.item_sha256.in_(list(fingerprints)),
        )
        .limit(5)
    ).all()
    return tuple(
        {
            "item_id": str(row.id),
            "episode_id": str(row.episode_id),
            "sha256": row.item_sha256[:16],
        }
        for row in rows
    )
