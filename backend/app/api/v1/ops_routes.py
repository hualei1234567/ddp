"""运行态运维路由（V4.1 FR-042）：投递队列健康快照。

独立文件的理由：运维视图与业务域无关（不按 Episode/标注/数据集划分），
混进任何业务路由都会让"谁该维护它"变模糊。

权限：``get_owner_scope``（owner 或平台管理员）。理由——队列健康暴露的是
系统内部的失败模式（哪个下游最先坏、积压多久），属于运维信息，不是所有
项目成员的日常需要；同时它不含 payload，即使误开也不会泄漏业务内容。

只读：不提供任何写操作。「清理失败事件」「重投」这类动作必须走既有 Worker
运维流程并留审计，不在 HTTP 面开洞。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import DbSession, get_owner_scope
from app.application.ops.dispatch_queue import read_dispatch_queue
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["ops"], prefix="/projects/{project_id}/ops")

OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


class DispatchQueueSnapshotOut(BaseModel):
    """投递队列健康快照。

    字段语义刻意保持"可直接展示"：运维看到一个数字就知道要不要动。
    ``stale`` 是**派生**结论（最老未处理超过阈值），不是独立事实——
    客户端不需要自己比时间。
    """

    pending: int = Field(ge=0, description="待处理（PENDING）事件数")
    published: int = Field(ge=0, description="已发布（PUBLISHED）事件数")
    failed: int = Field(ge=0, description="失败（FAILED）事件数")
    retrying: int = Field(ge=0, description="正在重试的事件数（attempts > 0 且仍 PENDING）")
    oldest_pending_age_seconds: int | None = Field(
        default=None,
        ge=0,
        description="最老一条待处理事件距今秒数；无待处理时为 null（不是 0）",
    )
    stale: bool = Field(description="是否疑似停滞（最老待处理超过部署阈值）；无待处理时为 false")
    failures_by_event_type: list[DispatchFailureBucketOut] = Field(
        default_factory=list,
        description="按事件类型聚合的失败计数（降序），便于定位首个损坏的下游",
    )
    generated_at: datetime = Field(description="快照生成时间（UTC，带时区）")


class DispatchFailureBucketOut(BaseModel):
    event_type: str = Field(min_length=1, description="事件类型（如 dataset.export.failed）")
    count: int = Field(ge=0, description="该类型的失败事件数")


@router.get(
    "/dispatch-queue",
    response_model=DispatchQueueSnapshotOut,
    summary="投递队列健康快照（积压/重试/失败分布）",
)
def get_dispatch_queue_snapshot(
    scope: OwnerScope,
    session: DbSession,
) -> DispatchQueueSnapshotOut:
    """读取当前项目的投递队列健康快照。

    只读聚合查询：不写库、不改动任何事件状态。项目隔离由 ``scope`` 强制，
    跨项目事件不会出现在本响应里。
    """
    snapshot = read_dispatch_queue(session, project_id=scope.project_id)
    return DispatchQueueSnapshotOut(
        pending=snapshot.counts.get("PENDING", 0),
        published=snapshot.counts.get("PUBLISHED", 0),
        failed=snapshot.counts.get("FAILED", 0),
        retrying=snapshot.retrying,
        oldest_pending_age_seconds=snapshot.oldest_pending_age_seconds,
        stale=snapshot.stale,
        failures_by_event_type=[
            DispatchFailureBucketOut(event_type=event_type, count=count)
            for event_type, count in snapshot.failures_by_event_type
        ],
        generated_at=snapshot.generated_at,
    )


__all__ = ["router"]
