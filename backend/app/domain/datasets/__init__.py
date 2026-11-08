"""阶段 4 数据集领域包。

只包含纯 Python 规则：Dataset/DatasetVersion 状态机、选择、去重、划分、
泄漏检查、质量报告与 Manifest。不依赖 FastAPI、SQLAlchemy 或对象存储。
"""

from app.domain.datasets.enums import (
    DatasetPurpose,
    DatasetStatus,
    DatasetUsagePolicy,
    DatasetVersionStatus,
    DedupKey,
    EmptySplitPolicy,
    GroupingKey,
    LeakageRule,
    LeakageStatus,
    QualityReportStatus,
    RetainRule,
    RoundingRule,
    SampleRole,
    Split,
    SplitStrategy,
    ValidityStatus,
)
from app.domain.datasets.errors import DatasetDomainError

__all__ = [
    "DatasetDomainError",
    "DatasetPurpose",
    "DatasetStatus",
    "DatasetUsagePolicy",
    "DatasetVersionStatus",
    "DedupKey",
    "EmptySplitPolicy",
    "GroupingKey",
    "LeakageRule",
    "LeakageStatus",
    "QualityReportStatus",
    "RetainRule",
    "RoundingRule",
    "SampleRole",
    "Split",
    "SplitStrategy",
    "ValidityStatus",
]
