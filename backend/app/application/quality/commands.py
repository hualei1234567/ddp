"""质量运行命令对象。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.quality.enums import QualityCategory, QualitySubjectType
from app.domain.quality.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class NewQualityRun:
    subject_type: QualitySubjectType
    subject_id: UUID
    categories: tuple[QualityCategory, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject_type, QualitySubjectType):
            raise DomainValidationError("subject_type 必须是 QualitySubjectType")
        if not isinstance(self.subject_id, UUID):
            raise DomainValidationError("subject_id 必须是 UUID")
        if self.categories is not None:
            categories = tuple(self.categories)
            if not categories:
                raise DomainValidationError("categories 不能为空")
            if any(not isinstance(item, QualityCategory) for item in categories):
                raise DomainValidationError("categories 只能包含 QualityCategory")
            if QualityCategory.OVERALL in categories:
                raise DomainValidationError("OVERALL 是派生类别，不能用于筛选规则")
            object.__setattr__(self, "categories", categories)


__all__ = ["NewQualityRun"]
