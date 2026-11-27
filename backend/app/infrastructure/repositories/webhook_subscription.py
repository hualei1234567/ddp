"""Webhook 订阅 SQLAlchemy 仓储（阶段 5）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.webhook.errors import WebhookNotConfiguredError
from app.domain.webhook.subscription import (
    WebhookSubscription,
    decrypt_secret,
    encrypt_secret,
)
from app.infrastructure.db.models.webhook_subscription import (
    WebhookSubscriptionModel as _Model,
)


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """投递目标：订阅 + **内存内**解密出的明文 secret。

    明文只在这个 DTO 里存在，不落库、不进日志、不进响应。
    """

    subscription: WebhookSubscription
    secret: str


class SqlWebhookSubscriptionRepository:
    """``webhook_subscriptions`` 读写仓储（不提交，由调用方的 UoW 统一提交）。"""

    def __init__(self, session: Session, *, encryption_key: bytes) -> None:
        self._session = session
        # 加密密钥来自部署配置；仅用于投递时内存解密，不落任何持久化形态
        self._encryption_key = encryption_key

    def add(self, scope: ProjectScope, subscription: WebhookSubscription) -> None:
        scope.ensure(subscription.project_id)
        now = datetime.now(UTC)
        self._session.add(
            _Model(
                id=subscription.id,
                project_id=subscription.project_id,
                url=subscription.url,
                secret_hash=subscription.secret_hash,
                secret_encrypted=encrypt_secret(
                    # 领域实体的临时明文字段——创建时携带，落库前加密
                    subscription.secret_plain or "",
                    self._require_key(),
                ),
                event_types=list(subscription.event_types),
                status=subscription.status,
                consecutive_failures=subscription.consecutive_failures,
                last_success_at=subscription.last_success_at,
                last_error_at=subscription.last_error_at,
                last_error_message=subscription.last_error_message,
                created_at=subscription.created_at or now,
                updated_at=subscription.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )

    def _require_key(self) -> bytes:
        """加密密钥用时校验：创建订阅才需要，空密钥明确报错。"""
        if not self._encryption_key:
            raise WebhookNotConfiguredError(
                "未配置 DDP_WEBHOOK_ENCRYPTION_KEY，无法创建 Webhook 订阅"
            )
        return self._encryption_key

    def find_url(self, scope: ProjectScope, url: str) -> WebhookSubscription | None:
        """按项目 + URL 查找（唯一约束的业务侧查询，用于 409 判定）。"""
        scope.ensure(scope.project_id)
        row = self._session.scalars(
            select(_Model).where(_Model.project_id == scope.project_id, _Model.url == url)
        ).first()
        return _to_domain(row) if row else None

    def find(self, scope: ProjectScope, subscription_id: UUID) -> WebhookSubscription | None:
        scope.ensure(scope.project_id)
        row = self._session.get(_Model, subscription_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _to_domain(row)

    def list_for_project(
        self, scope: ProjectScope, *, limit: int = 100
    ) -> tuple[WebhookSubscription, ...]:
        scope.ensure(scope.project_id)
        rows = self._session.scalars(
            select(_Model)
            .where(_Model.project_id == scope.project_id)
            .order_by(_Model.created_at.desc(), _Model.id)
            .limit(limit)
        ).all()
        return tuple(_to_domain(row) for row in rows)

    def replace_state(self, subscription_id: UUID, updated: WebhookSubscription) -> None:
        """投递后回写状态（成功/失败计数、暂停/恢复）。"""
        row = self._session.get(_Model, subscription_id)
        if row is None:
            return
        row.status = updated.status
        row.consecutive_failures = updated.consecutive_failures
        row.last_success_at = updated.last_success_at
        row.last_error_at = updated.last_error_at
        row.last_error_message = updated.last_error_message
        row.updated_at = updated.updated_at or datetime.now(UTC)

    def delete(self, scope: ProjectScope, subscription_id: UUID) -> bool:
        scope.ensure(scope.project_id)
        row = self._session.get(_Model, subscription_id)
        if row is None or row.project_id != scope.project_id:
            return False
        self._session.delete(row)
        return True

    def active_for_event(self, project_id: UUID, event_type: str) -> tuple[DeliveryTarget, ...]:
        """投递时查询：项目内 ACTIVE 且命中该事件类型的订阅，
        并**在内存内**解密出明文 secret 供签名使用。

        明文不落库、不进日志、不进响应；调用方用完即弃。
        """
        rows = self._session.scalars(
            select(_Model).where(
                _Model.project_id == project_id,
                _Model.status == "ACTIVE",
            )
        ).all()
        matched: list[DeliveryTarget] = []
        for row in rows:
            if event_type not in list(row.event_types or ()):
                continue
            plain = decrypt_secret(row.secret_encrypted, self._encryption_key)
            matched.append(DeliveryTarget(subscription=_to_domain(row), secret=plain))
        return tuple(matched)


def _to_domain(row: _Model) -> WebhookSubscription:
    return WebhookSubscription(
        id=row.id,
        project_id=row.project_id,
        url=row.url,
        secret_hash=row.secret_hash,
        event_types=tuple(row.event_types or ()),
        status=row.status,
        consecutive_failures=row.consecutive_failures,
        last_success_at=row.last_success_at,
        last_error_at=row.last_error_at,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
