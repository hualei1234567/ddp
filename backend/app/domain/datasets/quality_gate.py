"""Episode 质量门禁：把统一质量引擎的 Episode 结论接入数据集构造与导出。

本模块是**唯一权威口径**，不在流水线、用例或导出 Worker 里各写一份：

- 只看 Episode 的**最新**一次质量运行，排序键固定为 ``(created_at, id)`` 倒序；
- 最新运行缺失 → ``QUALITY_RUN_MISSING``，绝不把"没检查"当成通过；
- 最新结论不是 ``PASSED``（``FAILED`` / ``INCOMPLETE``）→ ``QUALITY_RUN_NOT_PASSED``；
- 要求分数时必须有分数，且 ``score >= min_score`` 才通过（等于阈值视为通过）；
- 结论/分数只来自 :class:`EpisodeQualityVerdict` 投影，本模块不查库、不重算质量。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasets.enums import ExclusionCode
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.validation import optional_aware_datetime
from app.domain.quality.enums import QualityRunStatus


@dataclass(frozen=True, slots=True)
class EpisodeQualityVerdict:
    """一个 Episode 最新质量运行的投影（由应用层批量读取后传入）。"""

    episode_id: UUID
    run_id: UUID
    status: QualityRunStatus | str
    score: float | None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        try:
            status = (
                self.status
                if isinstance(self.status, QualityRunStatus)
                else QualityRunStatus(self.status)
            )
        except ValueError as exc:
            raise DomainValidationError("质量运行结论取值非法") from exc
        object.__setattr__(self, "status", status)
        if self.score is not None:
            if isinstance(self.score, bool) or not isinstance(self.score, int | float):
                raise DomainValidationError("质量分数必须是数字")
            value = float(self.score)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise DomainValidationError("质量分数必须在 0 到 1 之间")
            object.__setattr__(self, "score", round(value, 4))
        object.__setattr__(
            self,
            "created_at",
            optional_aware_datetime(self.created_at, "created_at"),
        )


@dataclass(frozen=True, slots=True)
class QualityGateDecision:
    """单个 Episode 的门禁判定；``code`` 为 ``None`` 表示通过。"""

    episode_id: UUID
    code: ExclusionCode | None = None
    detail: str | None = None
    run_id: UUID | None = None

    @property
    def passed(self) -> bool:
        return self.code is None

    def to_payload(self) -> dict[str, object]:
        return {
            "episode_id": str(self.episode_id),
            "passed": self.passed,
            "code": self.code.value if self.code is not None else None,
            "detail": self.detail,
            "run_id": str(self.run_id) if self.run_id is not None else None,
        }


def _min_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError("min_quality_score 必须是 0 到 1 之间的数字")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise DomainValidationError("min_quality_score 必须在 0 到 1 之间")
    return score


def evaluate_quality_gate(
    episode_ids: Iterable[UUID],
    verdicts: Mapping[UUID, EpisodeQualityVerdict],
    *,
    min_score: float,
) -> tuple[QualityGateDecision, ...]:
    """按输入顺序去重判定；同一 Episode 只出现一次。"""
    threshold = _min_score(min_score)
    decisions: list[QualityGateDecision] = []
    seen: set[UUID] = set()
    for episode_id in episode_ids:
        if episode_id in seen:
            continue
        seen.add(episode_id)
        decisions.append(_evaluate_one(episode_id, verdicts.get(episode_id), threshold))
    return tuple(decisions)


def _evaluate_one(
    episode_id: UUID,
    verdict: EpisodeQualityVerdict | None,
    threshold: float,
) -> QualityGateDecision:
    if verdict is None:
        return QualityGateDecision(
            episode_id,
            code=ExclusionCode.QUALITY_RUN_MISSING,
            detail="缺少统一质量运行结论",
        )
    if verdict.status is not QualityRunStatus.PASSED:
        return QualityGateDecision(
            episode_id,
            code=ExclusionCode.QUALITY_RUN_NOT_PASSED,
            detail=f"最新质量运行结论为 {verdict.status.value}",
            run_id=verdict.run_id,
        )
    if verdict.score is None:
        return QualityGateDecision(
            episode_id,
            code=ExclusionCode.QUALITY_SCORE_MISSING,
            detail="最新质量运行没有可用的 OVERALL 分数",
            run_id=verdict.run_id,
        )
    if verdict.score < threshold:
        return QualityGateDecision(
            episode_id,
            code=ExclusionCode.QUALITY_SCORE_BELOW_THRESHOLD,
            detail=f"最新质量运行分数 {verdict.score:.4f} 低于阈值 {threshold:.4f}",
            run_id=verdict.run_id,
        )
    return QualityGateDecision(episode_id, run_id=verdict.run_id)


__all__ = [
    "EpisodeQualityVerdict",
    "QualityGateDecision",
    "evaluate_quality_gate",
]
