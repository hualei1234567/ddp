"""经营治理 HTTP 路由（N9 规模化运营）。

三个端点回答"用多少、剩多少、超没超"：

- ``GET /quotas``：本项目已配置的上限；
- ``PUT /quotas/{resource_kind}``：设置/调整上限（修改需 ``If-Match``）；
- ``GET /usage-report``：用量与配额的对照报表。

报表读的是离线快照（``usage_snapshots``），不会在请求线程扫全表；
快照由 ``python -m app.workers.usage_snapshots`` 定时刷新。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.governance_dependencies import GovernanceServicesDep
from app.api.v1.governance_schemas import (
    QuotaListOut,
    QuotaOut,
    QuotaSet,
    UsageReportOut,
)
from app.api.v1.preconditions import optional_expected_version
from app.domain.governance.enums import ResourceKind
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["governance"])

# 读取（列表/报表）只要求项目读权限；配置配额是写操作，viewer 由
# ``get_write_scope`` 直接拒绝（403），无需在函数体内再判一次。
ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
OptionalExpectedVersion = Annotated[int | None, Depends(optional_expected_version)]


@router.get("/quotas", response_model=QuotaListOut)
def list_quotas(
    scope: ReadScope,
    services: GovernanceServicesDep,
) -> QuotaListOut:
    """本项目全部配额配置。

    只返回**已配置**的条目；某类资源没有配额代表"未设上限"，报表里会以
    ``status=UNLIMITED`` 呈现。这里不做"每种资源补一条空行"，避免把
    "没配置"伪装成"配置为空"。
    """
    quotas = services.governance.list_quotas(scope)
    return QuotaListOut(items=[QuotaOut.from_domain(quota) for quota in quotas])


@router.put("/quotas/{resource_kind}", response_model=QuotaOut)
def set_quota(
    resource_kind: ResourceKind,
    payload: QuotaSet,
    request: Request,
    response: Response,
    scope: WriteScope,
    services: GovernanceServicesDep,
    expected_version: OptionalExpectedVersion = None,
) -> QuotaOut:
    """创建或修改一类资源的上限。

    - 新建：不需要 ``If-Match``；
    - 修改：必须带 ``If-Match: "<version>"``，缺失 428、版本不符 409，
      避免静默覆盖其他管理员的调整。
    """
    quota = services.governance.set_quota(
        scope,
        resource_kind,
        payload.limit,
        expected_version=expected_version,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = f'"{quota.version}"'
    return QuotaOut.from_domain(quota)


@router.get("/usage-report", response_model=UsageReportOut)
def get_usage_report(
    scope: ReadScope,
    services: GovernanceServicesDep,
) -> UsageReportOut:
    """用量—配额对照报表。

    每个条目都带 ``measured_at``（统计截止时间）与 ``measurement_source``
    （权威来源标识）。``used=null`` 表示尚无可用快照，不是 0。
    """
    report = services.governance.usage_report(scope)
    return UsageReportOut.from_domain(report)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


__all__ = ["router"]
