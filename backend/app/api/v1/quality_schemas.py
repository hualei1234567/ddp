"""质量运行 HTTP 请求模型。

请求只接受本批次开放的两类对象；``DATASET_VERSION`` 的规则源已注册，但入口
留给后续批次。``categories`` 是**可选**筛选：未知类别由 Pydantic 枚举直接
拒绝（统一映射为 ``validation_failed``），不会被静默忽略。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.quality.enums import QualityCategory

PublicQualitySubjectType = Literal["EPISODE", "DATASOURCE_IMPORT"]


class QualityRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: PublicQualitySubjectType
    subject_id: UUID
    categories: list[QualityCategory] | None = Field(default=None, min_length=1)


class QualityRunBatchCreate(BaseModel):
    """批量重跑：为一组 Episode 各排一次质检。

    只接受 ``EPISODE``：导入任务的批量重跑没有业务场景（一次导入本身就是
    一个对象），放开只会让调用方误以为支持。
    """

    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["EPISODE"]
    subject_ids: list[UUID] = Field(min_length=1, max_length=1000)


__all__ = [
    "PublicQualitySubjectType",
    "QualityRunBatchCreate",
    "QualityRunCreate",
]
