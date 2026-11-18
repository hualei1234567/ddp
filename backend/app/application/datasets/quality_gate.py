"""数据集质量门禁的应用层适配：把统一质量引擎的 Episode 运行投影成门禁输入。

只做三件事，不重算质量、不新增规则：

1. 按 Episode 批量取**最新一次** ``quality_run``（一次查询，避免 N+1）；
2. 把运行投影成 :class:`EpisodeQualityVerdict`；
3. 把门禁判定折叠成可回显的导出阻断摘要/明细。

判定口径本身在 ``domain.datasets.quality_gate``，本模块不复制。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from app.application.quality.ports import QualityRunRepository
from app.domain.datasets.errors import QualityGateFailedError
from app.domain.datasets.models import DatasetItem
from app.domain.datasets.quality_gate import (
    EpisodeQualityVerdict,
    QualityGateDecision,
    evaluate_quality_gate,
)
from app.domain.datasets.selection import EpisodeCandidate, SelectionFilters
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import QualitySubjectType

#: 导出阻断回显的最大条目数；完整问题规模以计数/truncated 表达，避免超大响应。
MAX_GATE_DETAILS = 20


class EpisodeQualityGate:
    """Episode 级质量门禁读取器（复用既有 quality_runs 仓储）。"""

    def __init__(self, runs: QualityRunRepository) -> None:
        self._runs = runs

    def verdicts(
        self,
        scope: ProjectScope,
        episode_ids: Iterable[UUID],
    ) -> Mapping[UUID, EpisodeQualityVerdict]:
        unique = tuple(dict.fromkeys(episode_ids))
        if not unique:
            return {}
        runs = self._runs.latest_for_subjects(
            scope,
            subject_type=QualitySubjectType.EPISODE,
            subject_ids=unique,
        )
        return {
            run.subject_id: EpisodeQualityVerdict(
                episode_id=run.subject_id,
                run_id=run.run_id,
                status=run.status,
                score=run.score,
                created_at=run.created_at,
            )
            for run in runs
        }

    def verdicts_for_selection(
        self,
        scope: ProjectScope,
        filters: SelectionFilters,
        candidates: Sequence[EpisodeCandidate],
    ) -> Mapping[UUID, EpisodeQualityVerdict] | None:
        """未启用门槛时返回 ``None``，调用方据此保持既有流水线行为不变。"""
        if not filters.quality_gate_required:
            return None
        return self.verdicts(scope, (item.episode_id for item in candidates))


def gate_failure_summary(
    decisions: Sequence[QualityGateDecision],
    *,
    min_score: float,
) -> str:
    blocked = [item for item in decisions if not item.passed]
    samples = "、".join(str(item.episode_id) for item in blocked[:3])
    suffix = " 等" if len(blocked) > 3 else ""
    return (
        f"{len(blocked)} 个 Episode 未达到质量门槛 {min_score:.4f}"
        f"（最新质量运行口径）：{samples}{suffix}"
    )


def gate_failure_details(
    decisions: Sequence[QualityGateDecision],
    *,
    min_score: float,
) -> dict[str, object]:
    blocked = [item for item in decisions if not item.passed]
    return {
        "min_quality_score": min_score,
        "blocked_episode_count": len(blocked),
        "episodes": [item.to_payload() for item in blocked[:MAX_GATE_DETAILS]],
        "truncated": len(blocked) > MAX_GATE_DETAILS,
    }


def evaluate_for_episodes(
    gate: EpisodeQualityGate,
    scope: ProjectScope,
    episode_ids: Sequence[UUID],
    *,
    min_score: float,
) -> tuple[QualityGateDecision, ...]:
    return evaluate_quality_gate(
        episode_ids,
        gate.verdicts(scope, episode_ids),
        min_score=min_score,
    )


def export_gate_decisions(
    gate: EpisodeQualityGate,
    scope: ProjectScope,
    filters: SelectionFilters,
    items: Sequence[DatasetItem],
) -> tuple[QualityGateDecision, ...] | None:
    """导出侧门禁判定；未启用 ``min_quality_score`` 时返回 ``None``。

    导出与 Worker 复核共用这一入口，避免两处各写一份"取哪些 Episode"。
    """
    if not filters.quality_gate_required or filters.min_quality_score is None:
        return None
    return evaluate_for_episodes(
        gate,
        scope,
        tuple(item.episode_id for item in items),
        min_score=filters.min_quality_score,
    )


def ensure_export_quality_gate(
    gate: EpisodeQualityGate,
    scope: ProjectScope,
    filters: SelectionFilters,
    items: Sequence[DatasetItem],
) -> None:
    """未通过时抛出可解释的 :class:`QualityGateFailedError`；未启用时直接返回。"""
    threshold = filters.min_quality_score
    decisions = export_gate_decisions(gate, scope, filters, items)
    if decisions is None or threshold is None:
        return
    if all(item.passed for item in decisions):
        return
    raise QualityGateFailedError(
        gate_failure_summary(decisions, min_score=threshold),
        details=gate_failure_details(decisions, min_score=threshold),
    )


def export_gate_failure_summary(
    gate: EpisodeQualityGate,
    scope: ProjectScope,
    filters: SelectionFilters,
    items: Sequence[DatasetItem],
) -> str | None:
    """Worker 侧复核：未启用或全部通过时返回 ``None``。"""
    threshold = filters.min_quality_score
    decisions = export_gate_decisions(gate, scope, filters, items)
    if decisions is None or threshold is None:
        return None
    if all(item.passed for item in decisions):
        return None
    return gate_failure_summary(decisions, min_score=threshold)


__all__ = [
    "MAX_GATE_DETAILS",
    "EpisodeQualityGate",
    "ensure_export_quality_gate",
    "evaluate_for_episodes",
    "export_gate_decisions",
    "export_gate_failure_summary",
    "gate_failure_details",
    "gate_failure_summary",
]
