"""黄金题熔断 HTTP 路由（V52-008，蓝图 N3 Honeypot 阶段 2）。

独立文件的理由与 ``token_ledger_routes.py`` 相同：熔断状态是标注员的
运维/治理视图（派单准入），不属于黄金集业务域本身；混进
``annotation_quality.py`` 会让"谁该维护它"变模糊。

权限：``get_owner_scope``（owner 或平台管理员）。理由——解除暂停改变
派单准入与结算冻结口径，是治理动作，不开放给 editor/viewer（对齐
审计日志与 Token 台账的先例）。

开关：``DDP_HONEYPOT_BREAKER_ENABLED`` 默认关闭时端点仍存在（契约路径
固定），行为退化为"读当前状态、resumed=false"——与派单不查、状态行
不写的口径一致，行为不变。

契约：路径只进 fragments（``paths.ops.yaml`` 增补
``annotationHoneypotResume``），本轮**不 bump openapi.yaml version**
（主控统一处理版本与引用集成；MEM-INC-017 的路径参数三坑已按
「声明参数 → 组件入 parameters: 段 → fragment 相对 $ref」执行）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import DbSession, get_owner_scope
from app.application.annotation.honeypot_wiring import resume
from app.core.config import Settings, get_settings
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["annotation-quality"], prefix="/projects/{project_id}")

OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class HoneypotResumeOut(BaseModel):
    """解除暂停后的状态快照。

    ``resumed=False`` 的三种情形（都返回 200，不区分——客户端按 state
    渲染即可）：开关关闭；无状态行（本来就没熔断）；本就 NORMAL
    （重复解除是幂等操作，不是错误）。
    """

    annotator_id: str = Field(description="标注员 ID")
    state: str = Field(description="解除后的熔断状态：NORMAL / PAUSED")
    resumed: bool = Field(description="本次调用是否实际发生了 NORMAL 化")
    checked_at: datetime = Field(description="状态快照时间（UTC，带时区）")


@router.post(
    "/annotation-honeypot/{annotator_id}/resume",
    response_model=HoneypotResumeOut,
    summary="解除标注员的黄金题熔断暂停（清窗口回 NORMAL）",
)
def resume_honeypot_pause(
    annotator_id: UUID,
    scope: OwnerScope,
    session: DbSession,
    settings: SettingsDep,
) -> HoneypotResumeOut:
    """解除指定标注员的黄金题熔断暂停。

    幂等：PAUSED → NORMAL 并清空窗口；本就 NORMAL 时原样返回。窗口
    清零后从空窗口重新累计（领域 ``resume`` 语义），不回放历史命中。
    只影响派单准入，不改动已有分配、提交与结算记录。
    """
    result = resume(
        session,
        scope.project_id,
        annotator_id,
        by=scope.actor_id,
        enabled=settings.honeypot_breaker_enabled,
    )
    # 请求级 Session 依赖成功返回不自动提交（见 get_session）；本路由是
    # 事务边界，显式提交。resume 是单行状态更新，无跨表事务需求。
    session.commit()
    return HoneypotResumeOut(
        annotator_id=result["annotator_id"],
        state=result["state"],
        resumed=result["resumed"],
        checked_at=datetime.now(UTC),
    )


__all__ = ["router"]
