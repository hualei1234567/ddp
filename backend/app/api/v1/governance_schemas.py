"""经营治理 API DTO（N9 规模化运营）。

响应字段是本轮**新增端点**的对外形状；契约文件
``packages/contracts/openapi.yaml`` 尚未声明这些路径（按任务要求不改契约），
精确清单见交付报告，待 System Architect 走契约变更流程。

`used` / `limit` / `remaining` / `exceeded` 一律允许 ``null``：
``used=null`` 表示"未测量"，不是 0；``limit=null`` 表示"未配置上限"。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.governance.enums import QuotaStatus, ResourceKind
from app.domain.governance.models import (
    QuotaComparison,
    ResourceQuota,
    UsageReport,
)

# BIGINT 上限：拒绝溢出数据库列的值，而不是让驱动在落库时报错。
_MAX_LIMIT = 9_223_372_036_854_775_807


class QuotaSet(BaseModel):
    """设置/修改某类资源上限。

    ``extra="forbid"``：客户端不能提交 ``project_id`` / ``version`` 等服务端
    权威值。``If-Match`` 通过请求头传递，不在 body 里。
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=0, le=_MAX_LIMIT)


class QuotaOut(BaseModel):
    quota_id: UUID
    project_id: UUID
    resource_kind: ResourceKind
    limit: int
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, quota: ResourceQuota) -> QuotaOut:
        return cls(
            quota_id=quota.quota_id,
            project_id=quota.project_id,
            resource_kind=quota.resource_kind,
            limit=quota.limit_value,
            version=quota.version,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
            created_by=quota.created_by,
            updated_by=quota.updated_by,
        )


class QuotaListOut(BaseModel):
    items: list[QuotaOut]


class UsageEntryOut(BaseModel):
    resource_kind: ResourceKind
    used: int | None
    limit: int | None
    remaining: int | None
    exceeded: bool | None
    status: QuotaStatus
    measurement_source: str | None
    measured_at: datetime | None

    @classmethod
    def from_domain(cls, entry: QuotaComparison) -> UsageEntryOut:
        return cls(
            resource_kind=entry.resource_kind,
            used=entry.used,
            limit=entry.limit,
            remaining=entry.remaining,
            exceeded=entry.exceeded,
            status=entry.status,
            measurement_source=entry.usage.source,
            measured_at=entry.usage.measured_at,
        )


class UsageReportOut(BaseModel):
    project_id: UUID
    generated_at: datetime
    entries: list[UsageEntryOut]

    @classmethod
    def from_domain(cls, report: UsageReport) -> UsageReportOut:
        return cls(
            project_id=report.project_id,
            generated_at=report.generated_at,
            entries=[UsageEntryOut.from_domain(entry) for entry in report.entries],
        )


__all__ = [
    "QuotaListOut",
    "QuotaOut",
    "QuotaSet",
    "UsageEntryOut",
    "UsageReportOut",
]
