"""Webhook 订阅用例（阶段 5）：创建 / 列表 / 删除（owner 口径）。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.ports import UnitOfWork
from app.domain.annotation.errors import DuplicateResourceError
from app.domain.identity.models import ProjectScope
from app.domain.webhook.subscription import (
    WebhookSubscription,
    hash_secret,
    normalize_url,
)
from app.infrastructure.repositories.webhook_subscription import (
    SqlWebhookSubscriptionRepository,
)
from sqlalchemy.exc import IntegrityError


@dataclass(frozen=True, slots=True)
class NewWebhookSubscription:
    """创建订阅的命令（明文 secret 由调用方提供，落库前加密）。"""

    url: str
    secret: str
    event_types: tuple[str, ...]


class WebhookSubscriptionUseCases:
    """订阅的 CRUD（写操作同事务提交）。"""

    def __init__(
        self,
        subscriptions: SqlWebhookSubscriptionRepository,
        uow: UnitOfWork,
    ) -> None:
        self._subscriptions = subscriptions
        self._uow = uow

    def create(self, scope: ProjectScope, command: NewWebhookSubscription) -> WebhookSubscription:
        url = normalize_url(command.url)
        if self._subscriptions.find_url(scope, url) is not None:
            raise DuplicateResourceError("项目内已存在同 URL 的 Webhook 订阅")

        now = datetime.now(UTC)
        subscription = WebhookSubscription(
            id=uuid4(),
            project_id=scope.project_id,
            url=url,
            secret_hash=hash_secret(command.secret),
            event_types=command.event_types,
            secret_plain=command.secret,  # 临时字段：落库前加密，之后置空
            created_at=now,
            updated_at=now,
        )
        try:
            self._subscriptions.add(scope, subscription)
            self._uow.commit()
        except IntegrityError as exc:
            # 并发创建撞唯一约束 → 409（同 URL 只允许一份）
            raise DuplicateResourceError("项目内已存在同 URL 的 Webhook 订阅") from exc
        # 落库后清掉临时明文字段（slots 数据类无 __dict__，用 replace）
        return replace(subscription, secret_plain=None)

    def list(self, scope: ProjectScope, *, limit: int = 100) -> tuple[WebhookSubscription, ...]:
        return self._subscriptions.list_for_project(scope, limit=limit)

    def delete(self, scope: ProjectScope, subscription_id: UUID) -> bool:
        deleted = self._subscriptions.delete(scope, subscription_id)
        if deleted:
            self._uow.commit()
        return deleted

    def get(self, scope: ProjectScope, subscription_id: UUID) -> WebhookSubscription | None:
        return self._subscriptions.find(scope, subscription_id)
