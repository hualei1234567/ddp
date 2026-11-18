"""一次质量运行所需的、已按项目作用域读取好的事实快照。

应用层先把检查对象依赖的数据一次性读齐，再交给各规则源。这样做的好处：

- 规则源不持有仓储、不查库，可单独测试（与标注域 ``RuleContext`` 同一思路）；
- 同一批事实被多个规则源共享，不会出现"两个源读到不同时刻的数据"；
- 读取范围在用例层显式可见，跨项目访问在读取阶段就返回 404。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.domain.annotation.schema import AnnotationSchema
from app.domain.annotation.submission import AnnotationRecord
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSourceImport
from app.domain.quality.enums import QualitySubjectType
from app.domain.quality.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class QualitySubjectFacts:
    subject_type: QualitySubjectType
    subject_id: UUID
    import_job: DataSourceImport | None = None
    assets: tuple[MediaAsset, ...] = ()
    annotation_records: tuple[AnnotationRecord, ...] = ()
    schemas: Mapping[UUID, AnnotationSchema] = field(default_factory=dict)
    task_policies: Mapping[UUID, Mapping[str, object]] = field(default_factory=dict)
    episode_bounds: Mapping[UUID, tuple[int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "assets", tuple(self.assets))
        object.__setattr__(self, "annotation_records", tuple(self.annotation_records))
        object.__setattr__(self, "schemas", dict(self.schemas))
        object.__setattr__(self, "task_policies", dict(self.task_policies))
        object.__setattr__(self, "episode_bounds", dict(self.episode_bounds))
        if not isinstance(self.subject_type, QualitySubjectType):
            raise DomainValidationError("subject_type 必须是 QualitySubjectType")
        if not isinstance(self.subject_id, UUID):
            raise DomainValidationError("subject_id 必须是 UUID")


__all__ = ["QualitySubjectFacts"]
