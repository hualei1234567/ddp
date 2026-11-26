"""Webhook 订阅路由（阶段 5）。

权限：**owner** 口径——订阅是项目级配置，能决定哪些事件推给哪个下游，
属运营敏感操作；普通 editor 不放开（避免误配把数据推到外部地址）。

契约：路径只进 fragments（``paths.ops.yaml``），**不 bump openapi version**。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import get_owner_scope
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.domain.annotation.errors import ResourceNotFoundError
from app.domain.identity.models import ProjectScope

router = APIRouter(
    tags=["webhook"],
    prefix="/projects/{project_id}/webhook-subscriptions",
)

OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


class WebhookEventTypesIn(BaseModel):
    """订阅的事件类型子集。"""

    event_types: list[str] = Field(
        min_length=1, max_length=50, description="订阅的事件类型（至少一个）"
    )


class WebhookCreateIn(BaseModel):
    """创建订阅。``secret`` 明文只在本次响应里返回，之后只存 hash。"""

    url: str = Field(min_length=1, max_length=2048, description="订阅地址（https 或本机 http）")
    secret: str = Field(min_length=8, max_length=256, description="验签密钥明文")
    event_types: list[str] = Field(min_length=1, max_length=50, description="订阅的事件类型")


class WebhookSubscriptionOut(BaseModel):
    """订阅视图（**不含** secret，无论明文还是 hash 都不暴露）。"""

    id: str
    url: str
    event_types: list[str]
    status: str = Field(description="ACTIVE / PAUSED")
    consecutive_failures: int
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error_message: str | None = None
    created_at: str | None = None


class WebhookSubscriptionPageOut(BaseModel):
    """订阅列表（与契约 ``WebhookSubscriptionPage`` 一致）。"""

    items: list[WebhookSubscriptionOut]


class WebhookCreatedOut(WebhookSubscriptionOut):
    """创建响应：**仅此一次**带明文 secret（方便下游保存）。"""

    secret: str = Field(description="验签密钥明文（仅创建时返回一次）")


@router.post("", response_model=WebhookCreatedOut, status_code=201)
def create_subscription(
    scope: OwnerScope,
    payload: WebhookCreateIn,
    use_cases: AnnotationUseCasesDep,
) -> WebhookCreatedOut:
    from app.application.webhook.subscriptions import NewWebhookSubscription

    created = use_cases.webhook_subscriptions.create(
        scope,
        NewWebhookSubscription(
            url=payload.url,
            secret=payload.secret,
            event_types=tuple(payload.event_types),
        ),
    )
    # 明文只在创建响应里出现一次；订阅视图一律不含
    return WebhookCreatedOut(
        id=str(created.id),
        url=created.url,
        event_types=list(created.event_types),
        status=created.status,
        consecutive_failures=created.consecutive_failures,
        last_success_at=_iso(created.last_success_at),
        last_error_at=_iso(created.last_error_at),
        last_error_message=created.last_error_message,
        created_at=_iso(created.created_at),
        secret=payload.secret,
    )


@router.get("", response_model=WebhookSubscriptionPageOut)
def list_subscriptions(
    scope: OwnerScope,
    use_cases: AnnotationUseCasesDep,
) -> WebhookSubscriptionPageOut:
    return WebhookSubscriptionPageOut(
        items=[
        WebhookSubscriptionOut(
            id=str(item.id),
            url=item.url,
            event_types=list(item.event_types),
            status=item.status,
            consecutive_failures=item.consecutive_failures,
            last_success_at=_iso(item.last_success_at),
            last_error_at=_iso(item.last_error_at),
            last_error_message=item.last_error_message,
            created_at=_iso(item.created_at),
        )
        for item in use_cases.webhook_subscriptions.list(scope)
        ]
    )


@router.delete("/{subscription_id}", status_code=204)
def delete_subscription(
    scope: OwnerScope,
    subscription_id: UUID,
    use_cases: AnnotationUseCasesDep,
) -> Response:
    if not use_cases.webhook_subscriptions.delete(scope, subscription_id):
        raise ResourceNotFoundError("Webhook 订阅", subscription_id)
    return Response(status_code=204)


def _iso(value: object) -> str | None:
    from datetime import datetime

    if isinstance(value, datetime):
        return value.isoformat()
    return None
