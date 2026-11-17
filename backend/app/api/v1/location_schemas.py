"""场地 API DTO（N9 多基地）。

请求模型 ``extra="forbid"``：客户端不能提交 ``project_id`` / ``version`` /
审计字段等服务端权威值。``kind`` 用枚举字面量做校验，非法取值由 Pydantic
直接拒绝（422），不进入领域层。

响应字段是本轮**新增端点**的对外形状；契约文件
``packages/contracts/openapi.yaml`` 尚未声明这两个路径（按任务要求不改契约），
缺口已写入交付报告，待 System Architect 走契约变更流程。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.locations.models import Location
from app.domain.locations.validation import (
    ADDRESS_MAX_LENGTH,
    NOTES_MAX_LENGTH,
)

LocationKindLiteral = Literal["SITE", "ROOM", "LAB"]


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    kind: LocationKindLiteral = "SITE"
    address: str | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    notes: str | None = Field(default=None, max_length=NOTES_MAX_LENGTH)


class LocationPatch(BaseModel):
    """PATCH 请求体；``code`` 不在其中（它是项目内业务键，改名应为新建行）。

    所有字段可选是为了支持"只改一个字段"。显式传 ``null`` 表示清空
    ``address`` / ``notes``；未出现的字段不参与更新。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: LocationKindLiteral | None = None
    address: str | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    notes: str | None = Field(default=None, max_length=NOTES_MAX_LENGTH)


class LocationOut(BaseModel):
    location_id: UUID
    project_id: UUID
    code: str
    name: str
    kind: str
    address: str | None
    notes: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, location: Location) -> LocationOut:
        return cls(
            location_id=location.location_id,
            project_id=location.project_id,
            code=location.code,
            name=location.name,
            kind=location.kind.value,
            address=location.address,
            notes=location.notes,
            version=location.version,
            created_at=location.created_at,
            updated_at=location.updated_at,
            created_by=location.created_by,
            updated_by=location.updated_by,
        )


__all__ = ["LocationCreate", "LocationOut", "LocationPatch"]
