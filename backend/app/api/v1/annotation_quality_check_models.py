"""质量检查 HTTP DTO。

``passed`` 是三态：``true`` 判定合规、``false`` 判定违规、``null`` 表示该规则
对这条记录没有结论（缺 Episode 边界、任务未配阈值）。客户端不得把 ``null``
渲染成"通过"。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.annotation.quality_check import QualityCheckRun, QualityFinding


class QualityFindingOut(BaseModel):
    annotation_id: UUID
    rule_id: str
    rule_version: int
    severity: str
    passed: bool | None
    message: str

    @classmethod
    def from_domain(cls, finding: QualityFinding) -> QualityFindingOut:
        return cls(
            annotation_id=finding.annotation_id,
            rule_id=finding.rule_id,
            rule_version=finding.rule_version,
            severity=finding.severity.value,
            passed=finding.passed,
            message=finding.message,
        )


class QualityCheckOut(BaseModel):
    run_id: UUID
    project_id: UUID
    submission_id: UUID
    rule_set_version: str
    status: str
    error_count: int
    warn_count: int
    started_at: datetime
    finished_at: datetime
    findings: list[QualityFindingOut]
    created_at: datetime | None
    created_by: UUID | None

    @classmethod
    def from_domain(cls, run: QualityCheckRun) -> QualityCheckOut:
        return cls(
            run_id=run.run_id,
            project_id=run.project_id,
            submission_id=run.submission_id,
            rule_set_version=run.rule_set_version,
            status=run.status.value,
            error_count=run.error_count,
            warn_count=run.warn_count,
            started_at=run.started_at,
            finished_at=run.finished_at,
            findings=[QualityFindingOut.from_domain(item) for item in run.findings],
            created_at=run.created_at,
            created_by=run.created_by,
        )


__all__ = ["QualityCheckOut", "QualityFindingOut"]
