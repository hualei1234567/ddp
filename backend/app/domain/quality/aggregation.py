"""六类聚合：把 finding 明细折叠成类别结论与运行结论。

聚合规则只在这里定义一次：

- 类别内出现 ``ERROR`` 状态 → 类别 ``ERROR``；
- 出现 ``ERROR`` 级 ``FAIL`` → 类别 ``FAIL``；
- 只有 ``WARN`` 级 ``FAIL`` → 类别 ``WARN``（提示，不阻断整体）；
- 至少一条 ``PASS`` → 类别 ``PASS``；
- 其余（全部 ``NOT_APPLICABLE``）→ ``NOT_APPLICABLE``。

运行结论：任一类 ``FAIL`` / ``ERROR`` → ``FAILED``；所有类别都没有可判定
结论 → ``INCOMPLETE``（绝不把"没检查"说成"通过"）；否则 ``PASSED``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.quality.enums import (
    QualityCategory,
    QualityCategoryStatus,
    QualityFindingStatus,
    QualityRunStatus,
    QualitySeverity,
)
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.models import QualityFinding
from app.domain.quality.rules import is_sha256_version  # noqa: F401 - 便于测试从聚合模块取口径

_CATEGORY_ORDER: tuple[QualityCategory, ...] = tuple(QualityCategory)


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category: QualityCategory
    status: QualityCategoryStatus
    finding_count: int
    pass_count: int
    fail_count: int
    not_applicable_count: int
    error_count: int
    score: float | None

    def to_payload(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "status": self.status.value,
            "finding_count": self.finding_count,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "not_applicable_count": self.not_applicable_count,
            "error_count": self.error_count,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class QualitySummary:
    categories: Mapping[QualityCategory, CategorySummary]
    status: QualityRunStatus
    score: float | None
    finding_count: int
    error_count: int
    warn_count: int

    def category_payload(self) -> dict[str, dict[str, object]]:
        """落库/响应用的稳定结构：六类始终齐全，缺数据即 ``NOT_APPLICABLE``。"""
        return {
            category.value: self.categories[category].to_payload() for category in _CATEGORY_ORDER
        }


def _round_score(total: float, count: int) -> float | None:
    if count == 0:
        return None
    return round(total / count, 4)


def _category_summary(
    category: QualityCategory,
    findings: list[QualityFinding],
) -> CategorySummary:
    pass_count = sum(1 for item in findings if item.status is QualityFindingStatus.PASS)
    fail_items = [item for item in findings if item.status is QualityFindingStatus.FAIL]
    not_applicable = sum(
        1 for item in findings if item.status is QualityFindingStatus.NOT_APPLICABLE
    )
    error_items = [item for item in findings if item.status is QualityFindingStatus.ERROR]
    scored = [
        float(item.score)
        for item in findings
        if item.status in {QualityFindingStatus.PASS, QualityFindingStatus.FAIL}
    ]
    if error_items:
        status = QualityCategoryStatus.ERROR
    elif any(item.severity is QualitySeverity.ERROR for item in fail_items):
        status = QualityCategoryStatus.FAIL
    elif fail_items:
        status = QualityCategoryStatus.WARN
    elif pass_count:
        status = QualityCategoryStatus.PASS
    else:
        status = QualityCategoryStatus.NOT_APPLICABLE
    return CategorySummary(
        category=category,
        status=status,
        finding_count=len(findings),
        pass_count=pass_count,
        fail_count=len(fail_items),
        not_applicable_count=not_applicable,
        error_count=len(error_items),
        score=_round_score(sum(scored), len(scored)),
    )


def summarize_findings(findings: Iterable[QualityFinding]) -> QualitySummary:
    """折叠 finding 明细；输入不得包含 ``OVERALL`` 类别。"""
    grouped: dict[QualityCategory, list[QualityFinding]] = {
        category: [] for category in QualityCategory if category is not QualityCategory.OVERALL
    }
    for finding in findings:
        if not isinstance(finding, QualityFinding):
            raise DomainValidationError("聚合输入只能包含 QualityFinding")
        category = finding.category
        if category is QualityCategory.OVERALL:
            raise DomainValidationError("OVERALL 是派生类别，不能作为 finding 输入")
        grouped[category].append(finding)

    summaries: dict[QualityCategory, CategorySummary] = {
        category: _category_summary(category, grouped[category]) for category in grouped
    }

    all_findings = [item for items in grouped.values() for item in items]
    error_count = sum(
        1
        for item in all_findings
        if item.status is QualityFindingStatus.FAIL and item.severity is QualitySeverity.ERROR
    )
    warn_count = sum(
        1
        for item in all_findings
        if item.status is QualityFindingStatus.FAIL and item.severity is QualitySeverity.WARN
    )
    scored = [
        float(item.score)
        for item in all_findings
        if item.status in {QualityFindingStatus.PASS, QualityFindingStatus.FAIL}
    ]
    overall_score = _round_score(sum(scored), len(scored))

    blocking = any(
        summary.status in {QualityCategoryStatus.FAIL, QualityCategoryStatus.ERROR}
        for summary in summaries.values()
    )
    judged = any(
        summary.status
        in {
            QualityCategoryStatus.PASS,
            QualityCategoryStatus.WARN,
            QualityCategoryStatus.FAIL,
            QualityCategoryStatus.ERROR,
        }
        for summary in summaries.values()
    )
    if blocking:
        run_status = QualityRunStatus.FAILED
        overall_status = QualityCategoryStatus.FAIL
    elif not judged:
        run_status = QualityRunStatus.INCOMPLETE
        overall_status = QualityCategoryStatus.NOT_APPLICABLE
    else:
        run_status = QualityRunStatus.PASSED
        overall_status = QualityCategoryStatus.PASS

    summaries[QualityCategory.OVERALL] = CategorySummary(
        category=QualityCategory.OVERALL,
        status=overall_status,
        finding_count=len(all_findings),
        pass_count=sum(summary.pass_count for summary in summaries.values()),
        fail_count=sum(summary.fail_count for summary in summaries.values()),
        not_applicable_count=sum(summary.not_applicable_count for summary in summaries.values()),
        error_count=sum(summary.error_count for summary in summaries.values()),
        score=overall_score,
    )
    return QualitySummary(
        categories=summaries,
        status=run_status,
        score=overall_score,
        finding_count=len(all_findings),
        error_count=error_count,
        warn_count=warn_count,
    )


__all__ = ["CategorySummary", "QualitySummary", "summarize_findings"]
