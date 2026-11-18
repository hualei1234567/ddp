"""统一 Quality Engine 领域包。

规则实现仍留在既有三套模块；本包只提供类别、规则声明、规则集版本、
结论实体与六类聚合。依赖方向：domain 不依赖 application / infrastructure。
"""

from app.domain.quality.aggregation import (
    CategorySummary,
    QualitySummary,
    summarize_findings,
)
from app.domain.quality.enums import (
    PUBLIC_SUBJECT_TYPES,
    RULE_CATEGORIES,
    QualityCategory,
    QualityCategoryStatus,
    QualityFindingStatus,
    QualityRunStatus,
    QualitySeverity,
    QualitySubjectType,
    coerce_category,
    coerce_subject_type,
)
from app.domain.quality.errors import (
    DomainValidationError,
    QualityDomainError,
    QualityEngineError,
    ResourceNotFoundError,
)
from app.domain.quality.models import (
    QualityFinding,
    QualityFindingDraft,
    QualityRun,
)
from app.domain.quality.rules import (
    QualityRuleDeclaration,
    RuleSet,
    compute_rule_set_version,
    is_sha256_version,
)

__all__ = [
    "PUBLIC_SUBJECT_TYPES",
    "RULE_CATEGORIES",
    "CategorySummary",
    "DomainValidationError",
    "QualityCategory",
    "QualityCategoryStatus",
    "QualityDomainError",
    "QualityEngineError",
    "QualityFinding",
    "QualityFindingDraft",
    "QualityFindingStatus",
    "QualityRuleDeclaration",
    "QualityRun",
    "QualityRunStatus",
    "QualitySeverity",
    "QualitySubjectType",
    "QualitySummary",
    "ResourceNotFoundError",
    "RuleSet",
    "coerce_category",
    "coerce_subject_type",
    "compute_rule_set_version",
    "is_sha256_version",
    "summarize_findings",
]
