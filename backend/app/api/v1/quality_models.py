"""质量运行 HTTP 响应模型。

与 ``packages/contracts/fragments/schemas.quality.yaml`` 对齐：

- ``categories`` 六类始终齐全（含派生 ``OVERALL``）；没有结论的类别显式
  返回 ``NOT_APPLICABLE``，客户端不得把它渲染成"通过"；
- ``score`` 为 ``null`` 表示没有可判定结论，不是 0 分；
- 结论明细走子资源分页，详情响应不内联无界数组。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.quality.enums import QualityCategory
from app.domain.quality.models import QualityFinding, QualityRun


class QualityCategorySummaryOut(BaseModel):
    category: str
    status: str
    finding_count: int
    pass_count: int
    fail_count: int
    not_applicable_count: int
    error_count: int
    score: float | None = None


class QualityFindingOut(BaseModel):
    finding_id: UUID
    run_id: UUID
    category: str
    rule_id: str
    rule_version: str
    severity: str
    status: str
    subject_ref: str
    message: str
    evidence: dict[str, object] | None = None
    score: float | None = None
    created_at: datetime

    @classmethod
    def from_domain(cls, finding: QualityFinding) -> QualityFindingOut:
        return cls(
            finding_id=finding.finding_id,
            run_id=finding.run_id,
            category=finding.category.value,
            rule_id=finding.rule_id,
            rule_version=finding.rule_version,
            severity=finding.severity.value,
            status=finding.status.value,
            subject_ref=finding.subject_ref,
            message=finding.message,
            evidence=dict(finding.evidence) if finding.evidence is not None else None,
            score=finding.score,
            created_at=finding.created_at,
        )


class QualityRunOut(BaseModel):
    run_id: UUID
    project_id: UUID
    subject_type: str
    subject_id: UUID
    rule_set_version: str
    status: str
    score: float | None = None
    finding_count: int
    error_count: int
    warn_count: int
    categories: list[QualityCategorySummaryOut]
    started_at: datetime
    finished_at: datetime
    created_at: datetime
    created_by: UUID

    @classmethod
    def from_domain(cls, run: QualityRun) -> QualityRunOut:
        categories = [
            QualityCategorySummaryOut(**run.category_summary[item.value])
            for item in QualityCategory
        ]
        return cls(
            run_id=run.run_id,
            project_id=run.project_id,
            subject_type=run.subject_type.value,
            subject_id=run.subject_id,
            rule_set_version=run.rule_set_version,
            status=run.status.value,
            score=run.score,
            finding_count=run.finding_count,
            error_count=run.error_count,
            warn_count=run.warn_count,
            categories=categories,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            created_by=run.created_by,
        )


__all__ = [
    "QualityCategorySummaryOut",
    "QualityFindingOut",
    "QualityRunOut",
]
