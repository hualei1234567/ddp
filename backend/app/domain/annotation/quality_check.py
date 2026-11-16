"""质量检查执行结果的领域对象。

这些对象是"已发生的检查事实"，不是聚合根：不参与状态机、不参与乐观锁，
只做构造期校验。放在领域层是因为落库前必须先保证不变式（计数与明细一致、
规则版本与明细一致），而这类校验属于领域规则，不属于仓储或路由。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.annotation.enums import QualityCheckStatus, QualityRuleSeverity
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.quality_rules import (
    RULE_DESCRIPTION_MAX_LENGTH,
    RULE_ID_MAX_LENGTH,
    resolve_status,
)
from app.domain.annotation.validation import (
    coerce_enum,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    positive_int,
    required_uuid,
    validate_sha256,
)


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """一条规则对一条记录的判定结果。

    ``passed is None`` 表示"该规则对此记录没有结论"（``applicable=False``），
    与 ``passed=True``（判定合规）严格区分；两者在计数里都不算 ERROR。
    """

    annotation_id: UUID
    rule_id: str
    rule_version: int
    severity: QualityRuleSeverity | str
    passed: bool | None
    message: str

    def __post_init__(self) -> None:
        required_uuid(self.annotation_id, "annotation_id")
        object.__setattr__(
            self,
            "rule_id",
            normalized_text(self.rule_id, "rule_id", max_length=RULE_ID_MAX_LENGTH),
        )
        object.__setattr__(self, "rule_version", positive_int(self.rule_version, "rule_version"))
        object.__setattr__(
            self,
            "severity",
            coerce_enum(self.severity, QualityRuleSeverity, "severity"),
        )
        if self.passed is not None and not isinstance(self.passed, bool):
            raise DomainValidationError("finding.passed 只能是布尔值或空")
        object.__setattr__(
            self,
            "message",
            normalized_text(
                self.message,
                "finding.message",
                max_length=RULE_DESCRIPTION_MAX_LENGTH,
            ),
        )

    @property
    def is_violation(self) -> bool:
        """明确判为不通过；``passed is None`` 不是违规。"""
        return self.passed is False


def summarize_findings(findings: tuple[QualityFinding, ...]) -> tuple[int, int]:
    """按严重级别统计违规数；``passed is None`` 不计入任何一侧。"""
    error_count = 0
    warn_count = 0
    for item in findings:
        if not item.is_violation:
            continue
        if item.severity is QualityRuleSeverity.ERROR:
            error_count += 1
        else:
            warn_count += 1
    return error_count, warn_count


@dataclass(frozen=True, slots=True)
class QualityCheckRun:
    """一次质量检查的完整结果（头 + 明细）。"""

    run_id: UUID
    project_id: UUID
    submission_id: UUID
    rule_set_version: str
    status: QualityCheckStatus | str
    error_count: int
    warn_count: int
    started_at: datetime
    finished_at: datetime
    findings: tuple[QualityFinding, ...] = ()
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "project_id", "submission_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "rule_set_version",
            validate_sha256(self.rule_set_version, "rule_set_version"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, QualityCheckStatus, "status"),
        )
        object.__setattr__(self, "error_count", non_negative_int(self.error_count, "error_count"))
        object.__setattr__(self, "warn_count", non_negative_int(self.warn_count, "warn_count"))
        for name in ("started_at", "finished_at"):
            value = optional_aware_datetime(getattr(self, name), name)
            if value is None:
                raise DomainValidationError(f"{name} 不能为空")
            object.__setattr__(self, name, value)
        if self.finished_at < self.started_at:
            raise DomainValidationError("finished_at 不得早于 started_at")
        findings = tuple(self.findings)
        for item in findings:
            if not isinstance(item, QualityFinding):
                raise DomainValidationError("findings 只能包含 QualityFinding")
        object.__setattr__(self, "findings", findings)
        expected_errors, expected_warns = summarize_findings(findings)
        # 计数与明细必须一致：两处各算一次就会漂移，写库前先钉死。
        if self.error_count != expected_errors or self.warn_count != expected_warns:
            raise DomainValidationError(
                "error_count/warn_count 与 findings 明细不一致："
                f"声明 ({self.error_count}, {self.warn_count})，"
                f"明细 ({expected_errors}, {expected_warns})"
            )
        if self.status is not resolve_status(expected_errors):
            raise DomainValidationError(
                f"status 与 ERROR 明细不一致：应为 {resolve_status(expected_errors).value}"
            )
        object.__setattr__(
            self,
            "created_at",
            optional_aware_datetime(self.created_at, "created_at"),
        )

    def to_header_payload(self) -> dict[str, object]:
        """检查头部的稳定 JSON 载荷（不含明细与时间戳顺序依赖）。"""
        return {
            "run_id": str(self.run_id),
            "project_id": str(self.project_id),
            "submission_id": str(self.submission_id),
            "rule_set_version": self.rule_set_version,
            "status": self.status.value,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "findings": [
                {
                    "annotation_id": str(item.annotation_id),
                    "rule_id": item.rule_id,
                    "rule_version": item.rule_version,
                    "severity": item.severity.value,
                    "passed": item.passed,
                    "message": item.message,
                }
                for item in self.findings
            ],
        }


@dataclass(frozen=True, slots=True)
class QualityEvaluation:
    """规则集对一份提交的求值结果（尚未落库，因此没有 run_id）。"""

    findings: tuple[QualityFinding, ...]
    error_count: int
    warn_count: int
    status: QualityCheckStatus

    def __post_init__(self) -> None:
        findings = tuple(self.findings)
        if not isinstance(findings, tuple) or any(
            not isinstance(item, QualityFinding) for item in findings
        ):
            raise DomainValidationError("findings 只能包含 QualityFinding")
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "error_count", non_negative_int(self.error_count, "error_count"))
        object.__setattr__(self, "warn_count", non_negative_int(self.warn_count, "warn_count"))
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, QualityCheckStatus, "status"),
        )


__all__ = [
    "QualityCheckRun",
    "QualityEvaluation",
    "QualityFinding",
    "summarize_findings",
]
