"""V51-017 保留策略（retention）领域包。

只包含纯 Python 规则：引用影响快照的数据形状、动作分类与"是否允许销毁内容"
的判定。不依赖 FastAPI / SQLAlchemy / 对象存储，因此可以在毫秒级单测里
穷举所有组合。

为什么单独成包而不是塞进 ``datasets`` 或 ``ingestion``：这条规则同时裁决
两个聚合——``RawAsset``（写入口在 ingestion 域）与 ``DatasetVersion``
（冻结入口在 datasets 域）。放进任何一侧都会让另一侧反向依赖它。
"""

from app.domain.retention.models import (
    FrozenDatasetReference,
    ReferenceImpact,
    ReferenceTargetKind,
    RetentionVerdict,
)
from app.domain.retention.policy import (
    CONTENT_DISCARDING_ACTIONS,
    CONTENT_PRESERVING_ACTIONS,
    is_content_discarding,
    retention_verdict,
)

__all__ = [
    "CONTENT_DISCARDING_ACTIONS",
    "CONTENT_PRESERVING_ACTIONS",
    "FrozenDatasetReference",
    "ReferenceImpact",
    "ReferenceTargetKind",
    "RetentionVerdict",
    "is_content_discarding",
    "retention_verdict",
]
