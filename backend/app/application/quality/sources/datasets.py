"""数据集质量报告的适配器（规则实现仍在 ``domain.datasets`` 质量流水线）。

数据集质量不是"逐条规则函数"，而是一份带阻断项/警告的报告。统一引擎把
"报告是否 READY"作为门禁规则接入，并把阻断项与警告作为 evidence 回显；
不重新计算划分、去重或泄漏，避免复制既有流水线。

适用对象：``DATASET_VERSION``。本批次 API 不开放该对象类型；规则源先注册
并通过单元测试验证，等后续批次接入数据集版本入口时直接复用。
"""

from __future__ import annotations

from app.application.datasets.ports import DatasetReportRepository
from app.application.quality.facts import QualitySubjectFacts
from app.domain.datasets.quality import QualityReport
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySeverity,
    QualitySubjectType,
)
from app.domain.quality.models import QualityFindingDraft
from app.domain.quality.rules import QualityRuleDeclaration

SOURCE_ID = "dataset_quality"
RULE_ID = "dataset_quality_gate"
#: 当前数据集质量生成器的版本口径；与 ``QualityReport`` 的默认值保持一致。
RULE_VERSION = "dataset-quality/1.0.0"
_EVIDENCE_ISSUE_LIMIT = 20
_EVIDENCE_ISSUE_MAX_LENGTH = 200

_DECLARATIONS: tuple[QualityRuleDeclaration, ...] = (
    QualityRuleDeclaration(
        rule_id=RULE_ID,
        category=QualityCategory.METADATA,
        version=RULE_VERSION,
        severity=QualitySeverity.ERROR,
        source=SOURCE_ID,
        description="数据集版本必须存在质量报告且状态为 READY（无阻断项）",
        subject_types=(QualitySubjectType.DATASET_VERSION,),
    ),
)


class DatasetQualityRuleSource:
    """把数据集质量报告接入统一引擎。"""

    source_id = SOURCE_ID
    declarations = _DECLARATIONS

    def __init__(self, reports: DatasetReportRepository) -> None:
        self._reports = reports

    def evaluate(
        self,
        scope: ProjectScope,
        facts: QualitySubjectFacts,
    ) -> tuple[QualityFindingDraft, ...]:
        declaration = _DECLARATIONS[0]
        report = self._reports.latest_quality(scope, facts.subject_id)
        if report is None:
            return (
                QualityFindingDraft(
                    rule_id=declaration.rule_id,
                    rule_version=declaration.version,
                    category=declaration.category,
                    severity=declaration.severity,
                    status=QualityFindingStatus.ERROR,
                    message="该数据集版本尚无质量报告，无法判定",
                    evidence={"reason": "dataset_quality_report_missing"},
                ),
            )
        return (_draft_from_report(declaration, report),)


def _draft_from_report(
    declaration: QualityRuleDeclaration,
    report: QualityReport,
) -> QualityFindingDraft:
    ready = report.is_ready
    evidence = {
        "report_sha256": report.report_sha256,
        "report_status": str(getattr(report.status, "value", report.status)),
        "candidate_count": report.candidate_count,
        "selected_count": report.selected_count,
        "blocking_issues": _bounded_issues(report.blocking_issues),
        "warnings": _bounded_issues(report.warnings),
    }
    if ready:
        message = (
            f"质量报告 READY：候选 {report.candidate_count} 条，" f"选中 {report.selected_count} 条"
        )
    else:
        message = f"质量报告存在 {len(report.blocking_issues)} 个阻断项，" "不得用于冻结或训练"
    return QualityFindingDraft(
        rule_id=declaration.rule_id,
        # 历史结论必须记录**当时**的生成器版本；存量报告可能由旧版生成。
        rule_version=report.generator_version,
        category=declaration.category,
        severity=declaration.severity,
        status=QualityFindingStatus.PASS if ready else QualityFindingStatus.FAIL,
        message=message,
        evidence=evidence,
        score=1.0 if ready else 0.0,
    )


def _bounded_issues(issues: tuple[str, ...]) -> list[str]:
    """evidence 有大小上限：只保留前 N 条并截断单条长度，不因报告过大而拒绝。"""
    return [str(item)[:_EVIDENCE_ISSUE_MAX_LENGTH] for item in issues[:_EVIDENCE_ISSUE_LIMIT]]


def declarations_for(subject_type: QualitySubjectType) -> tuple[QualityRuleDeclaration, ...]:
    return tuple(item for item in _DECLARATIONS if item.applies_to(subject_type))


__all__ = [
    "RULE_ID",
    "RULE_VERSION",
    "SOURCE_ID",
    "DatasetQualityRuleSource",
    "declarations_for",
]
