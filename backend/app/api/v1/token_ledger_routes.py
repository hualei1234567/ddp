"""Token 成本台账运维路由（V52-010，蓝图 N6）。

独立文件的理由与 ``ops_routes.py`` 相同：成本视图与业务域无关（按项目
聚合，不按 Episode/标注/数据集划分），混进业务路由会让"谁该维护它"变模糊。

权限：``get_owner_scope``（owner 或平台管理员）。理由——成本暴露的是
供应商账单折算（价格表属部署配置），且 ROI 口径（时薪）是企业内部信息，
不是所有项目成员的日常需要。

只读：不提供任何写操作。计量行由调用链可选注入 recorder 登记应用层通道。

契约：路径只进 fragments（``paths.ops.yaml`` 增补 token-ledger），本轮
**不 bump openapi.yaml version**（主控统一处理版本与引用集成）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import DbSession, get_owner_scope
from app.application.ops.token_ledger import read_cost_ledger
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["ops"], prefix="/projects/{project_id}/ops")

OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


class ModelPurposeLedgerOut(BaseModel):
    """单个 ``(model_id, purpose)`` 桶的累计计量。

    金额字段用字符串承载（契约 ``type: string``）：货币值经 JSON 双精度
    会丢精度，与 MEM-INC-008 的纳秒时间戳同族问题。
    """

    model_id: str = Field(min_length=1, description="模型 ID（成本归属键）")
    purpose: str = Field(description="计量用途：PRE_LABEL / LLM_JUDGE / EMBED")
    call_count: int = Field(ge=0, description="该桶的 LLM 调用次数")
    input_tokens: int = Field(ge=0, description="输入 token 合计")
    output_tokens: int = Field(ge=0, description="输出 token 合计")
    total_tokens: int = Field(ge=0, description="输入 + 输出 token 合计")
    cost_estimate: str | None = Field(
        default=None,
        description=(
            "该桶的成本合计（USD，十进制字符串，4 位小数）；"
            "null 表示该模型未配置价格——不是 0（0 是免费，null 是漏配）"
        ),
    )
    cost_estimated_tokens: int = Field(
        ge=0,
        description="有价可算部分的 token 数；未配价模型的 tokens 不计入",
    )


class CostLedgerOut(BaseModel):
    """项目级 Token 成本台账。"""

    project_id: str = Field(description="项目 ID")
    entries: list[ModelPurposeLedgerOut] = Field(
        description="按 (model_id, purpose) 聚合的条目（确定性排序）；无计量数据时为空数组"
    )
    total_cost_estimate: str | None = Field(
        default=None,
        description="全项目有价部分成本合计；全未配价时为 null（不是 0）",
    )
    generated_at: datetime = Field(description="快照生成时间（UTC，带时区）")


@router.get(
    "/token-ledger",
    response_model=CostLedgerOut,
    summary="Token 成本台账（按模型/用途聚合的调用、tokens 与成本）",
)
def get_token_cost_ledger(
    scope: OwnerScope,
    session: DbSession,
) -> CostLedgerOut:
    """读取当前项目的 Token 成本台账快照。

    只读聚合查询：不写库、不改动任何计量行。项目隔离由 ``scope`` 强制，
    跨项目的调用记录不会出现在本响应里。
    """
    snapshot = read_cost_ledger(session, project_id=scope.project_id)
    return CostLedgerOut(
        project_id=str(snapshot.project_id),
        entries=[
            ModelPurposeLedgerOut(
                model_id=entry.model_id,
                purpose=entry.purpose.value,
                call_count=entry.call_count,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                total_tokens=entry.total_tokens,
                cost_estimate=None if entry.cost_estimate is None else str(entry.cost_estimate),
                cost_estimated_tokens=entry.cost_estimated_tokens,
            )
            for entry in snapshot.entries
        ],
        total_cost_estimate=(
            None if snapshot.total_cost_estimate is None else str(snapshot.total_cost_estimate)
        ),
        generated_at=snapshot.generated_at,
    )


__all__ = ["router"]
