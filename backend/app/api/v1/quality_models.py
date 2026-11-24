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

from app.domain.quality.diff import DiffKind, QualityRunDiff
from app.domain.quality.enums import QualityCategory
from app.domain.quality.evidence import EvidenceLocation, EvidenceTimeRange
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


class QualityEvidenceTimeRangeOut(BaseModel):
    """证据时间区间（绝对纳秒，十进制字符串）。

    用字符串而不是整数：纳秒约 ``1.8e18``，超出 JSON 数字在浏览器里的精确
    表示范围（``2^53``）。写成数字会让前端静默定位到错误的时间点。
    """

    start_time_ns: str
    end_time_ns: str


class QualityEvidenceLocationOut(BaseModel):
    """一条质量结论的**证据定位**（V51-010 / FR-025）。

    三个字段都可空且相互独立：不是每条结论都有时间/流/字段维度。整体为
    ``null`` 表示该结论无法定位——界面必须如实展示"无定位"，不能凭空
    选一个位置跳转。
    """

    time_range: QualityEvidenceTimeRangeOut | None = None
    stream_id: str | None = None
    field_path: str | None = None

    @classmethod
    def from_domain(cls, location: EvidenceLocation | None) -> QualityEvidenceLocationOut | None:
        if location is None:
            return None
        time_range = (
            location.time_range if isinstance(location.time_range, EvidenceTimeRange) else None
        )
        return cls(
            time_range=(
                QualityEvidenceTimeRangeOut(
                    start_time_ns=str(time_range.start_time_ns),
                    end_time_ns=str(time_range.end_time_ns),
                )
                if time_range is not None
                else None
            ),
            stream_id=location.stream_id,
            field_path=location.field_path,
        )


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
    evidence_location: QualityEvidenceLocationOut | None = None
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
            evidence_location=QualityEvidenceLocationOut.from_domain(finding.evidence_location),
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


class QueuedQualityRunOut(BaseModel):
    """已入队但尚未执行的运行。

    与 :class:`QualityRunOut` 的差别是**没有结论**：``status`` / ``score`` /
    六类聚合 / 起止时间都为空。契约里单列一个模型，避免让调用方以为
    ``status=null`` 是"检查通过"——那正是 ADR-0042 要消除的误读。
    """

    run_id: UUID
    project_id: UUID
    subject_type: str
    subject_id: UUID
    rule_set_version: str
    lifecycle: str
    max_attempts: int
    created_at: datetime
    created_by: UUID

    @classmethod
    def from_domain(cls, run: QualityRun) -> QueuedQualityRunOut:
        return cls(
            run_id=run.run_id,
            project_id=run.project_id,
            subject_type=run.subject_type.value,
            subject_id=run.subject_id,
            rule_set_version=run.rule_set_version,
            lifecycle=run.lifecycle.value,
            max_attempts=run.max_attempts,
            created_at=run.created_at,
            created_by=run.created_by,
        )


class QualityFindingDiffOut(BaseModel):
    rule_id: str
    subject_ref: str
    kind: str
    baseline_status: str | None = None
    candidate_status: str | None = None


class QualityRunDiffOut(BaseModel):
    baseline_run_id: UUID
    candidate_run_id: UUID
    findings: list[QualityFindingDiffOut]
    new_count: int
    resolved_count: int
    unchanged_count: int
    changed_count: int
    has_regression: bool

    @classmethod
    def from_domain(cls, diff: QualityRunDiff) -> QualityRunDiffOut:
        counts = diff.counts
        return cls(
            baseline_run_id=diff.baseline_run_id,  # type: ignore[arg-type]
            candidate_run_id=diff.candidate_run_id,  # type: ignore[arg-type]
            findings=[
                QualityFindingDiffOut(
                    rule_id=item.rule_id,
                    subject_ref=item.subject_ref,
                    kind=item.kind.value,
                    baseline_status=(
                        item.baseline_status.value if item.baseline_status is not None else None
                    ),
                    candidate_status=(
                        item.candidate_status.value if item.candidate_status is not None else None
                    ),
                )
                for item in diff.findings
            ],
            new_count=counts[DiffKind.NEW],
            resolved_count=counts[DiffKind.RESOLVED],
            unchanged_count=counts[DiffKind.UNCHANGED],
            changed_count=counts[DiffKind.CHANGED],
            has_regression=diff.has_regression,
        )


__all__ = [
    "QualityCategorySummaryOut",
    "QualityEvidenceLocationOut",
    "QualityEvidenceTimeRangeOut",
    "QualityFindingDiffOut",
    "QualityFindingOut",
    "QualityRunDiffOut",
    "QualityRunOut",
    "QueuedQualityRunOut",
]
