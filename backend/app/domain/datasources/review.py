"""导入产出的数据审核：审核记录与质量报告。

职责分离原则复用标注域的做法（`domain/annotation/`）：**导入执行人不得充当
唯一审核人**。这里记录审核结论与依据，不复制标注域的状态机——审核对象不同
（原始媒体 vs 标注结果），强行复用会造成语义混淆。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.datasources.errors import DomainValidationError


class ReviewDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NEEDS_FIX = "NEEDS_FIX"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


REVIEW_REASON_MAX = 2000
_TERMINAL = frozenset({ReviewStatus.APPROVED, ReviewStatus.REJECTED})


@dataclass(frozen=True, slots=True)
class DataReview:
    """对一个导入结果的审核记录。"""

    review_id: UUID
    project_id: UUID
    import_id: UUID
    status: ReviewStatus | str
    decision: ReviewDecision | str | None
    reason: str | None
    reviewer_id: UUID | None
    submitted_by: UUID
    created_at: datetime
    decided_at: datetime | None = None
    row_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            ReviewStatus(self.status) if not isinstance(self.status, ReviewStatus) else self.status,
        )
        if self.decision is not None:
            object.__setattr__(
                self,
                "decision",
                (
                    ReviewDecision(self.decision)
                    if not isinstance(self.decision, ReviewDecision)
                    else self.decision
                ),
            )
        if self.reason is not None and len(self.reason) > REVIEW_REASON_MAX:
            raise DomainValidationError(f"reason 超过 {REVIEW_REASON_MAX} 字符")
        if self.row_version < 1:
            raise DomainValidationError("row_version 必须大于等于 1")

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def decide(
        self,
        *,
        decision: ReviewDecision | str,
        reviewer_id: UUID,
        reason: str | None,
        now: datetime,
    ) -> DataReview:
        """给出审核结论。

        三条硬规则：
        1. 终态不可再次裁决；
        2. **审核人不得是提交人**（职责分离，与标注域同一原则）；
        3. REJECT / NEEDS_FIX 必须写原因——否则被拒方不知道改什么。
        """
        from dataclasses import replace

        if self.is_terminal:
            raise DomainValidationError("该审核已结束，不能再次裁决")
        if reviewer_id == self.submitted_by:
            raise DomainValidationError("审核人不得是导入提交人（职责分离）")
        target = ReviewDecision(decision)
        if target is not ReviewDecision.ACCEPT and not (reason or "").strip():
            raise DomainValidationError("拒绝或要求修复必须填写原因")
        status = ReviewStatus.APPROVED if target is ReviewDecision.ACCEPT else ReviewStatus.REJECTED
        return replace(
            self,
            status=status,
            decision=target,
            reason=reason,
            reviewer_id=reviewer_id,
            decided_at=now,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """一条质量规则的结果。``detail`` 只放可核对的事实，不放推测。"""

    rule_id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    """一次导入的质量报告。

    `docs/ddp/07` §7 要求报告需说明**样本范围、规则版本、通过率与失败原因**，
    因此这几个字段都在这里显式存在，而不是只给一个总分。
    """

    import_id: UUID
    project_id: UUID
    rule_version: str
    sample_scope: str
    total_assets: int
    total_episodes: int
    findings: tuple[QualityFinding, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.findings)

    @property
    def failures(self) -> tuple[QualityFinding, ...]:
        return tuple(item for item in self.findings if not item.passed)

    def to_payload(self) -> Mapping[str, object]:
        """稳定序列化；失败也带原因，不返回空数组掩盖问题。"""
        return {
            "import_id": str(self.import_id),
            "rule_version": self.rule_version,
            "sample_scope": self.sample_scope,
            "total_assets": self.total_assets,
            "total_episodes": self.total_episodes,
            "passed": self.passed,
            "findings": [
                {"rule_id": f.rule_id, "passed": f.passed, "detail": f.detail}
                for f in self.findings
            ],
            "failures": [{"rule_id": f.rule_id, "detail": f.detail} for f in self.failures],
        }
