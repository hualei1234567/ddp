"""双标一致性结论 HTTP DTO。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.application.annotation.consistency import DoubleAnnotationConsistencyResult


class DoubleAnnotationConsistencyOut(BaseModel):
    task_id: UUID
    project_id: UUID
    review_mode: str
    status: str
    metric: str | None
    threshold: float | None
    value: float | None
    passed: bool | None
    sample_size: int
    episode_count: int
    double_episode_count: int
    annotator_count: int
    missing_episode_ids: list[UUID]
    auto_approve_enabled: bool
    auto_approved: bool
    arbitration_required: bool
    arbitration_ids: list[UUID]
    details: list[dict[str, object]]
    evaluated_at: datetime
    message: str | None

    @classmethod
    def from_result(
        cls,
        result: DoubleAnnotationConsistencyResult,
    ) -> DoubleAnnotationConsistencyOut:
        return cls(
            task_id=result.task_id,
            project_id=result.project_id,
            review_mode=result.review_mode,
            status=result.status,
            metric=result.metric,
            threshold=result.threshold,
            value=result.value,
            passed=result.passed,
            sample_size=result.sample_size,
            episode_count=result.episode_count,
            double_episode_count=result.double_episode_count,
            annotator_count=result.annotator_count,
            missing_episode_ids=list(result.missing_episode_ids),
            auto_approve_enabled=result.auto_approve_enabled,
            auto_approved=result.auto_approved,
            arbitration_required=result.arbitration_required,
            arbitration_ids=list(result.arbitration_ids),
            details=[dict(item) for item in result.details],
            evaluated_at=result.evaluated_at,
            message=result.message,
        )


__all__ = ["DoubleAnnotationConsistencyOut"]
