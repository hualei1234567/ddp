"""对外投递登记的接线（阶段 6）。

## 为什么用「包装 outbox 仓储」而不是在每个用例里加一行

标注域的事件写入有 30+ 个调用点，全部走 ``record_change``（唯一权威实现）。
若在每个用例里显式调登记器，会出现两个问题：

1. **漏一处就少一条事件**——外部集成最难发现的就是"某条事件没推出去"；
2. **规则散落**——「哪些事件允许出站」这条规则会被抄到多处。

包装 outbox 仓储把登记收敛到**唯一一个写入点**：``record_change`` → outbox
``enqueue`` → 登记器。这样：

- 白名单（:data:`app.application.webhook.outbound.OUTBOUND_EVENT_TYPES`）
  只在登记器一处判断；
- 业务用例**零签名变更**——不需要给 30+ 个用例类加参数，既有测试全绿；
- 登记与业务写入仍在**同一事务**（共用 Session）：业务回滚则不投递。

## 事务与幂等

登记器在同一个 Session 里写入 ``webhook_outbox``，唯一约束
``(subscription_id, event_id)`` 保证「同一事件同一订阅」只登记一次。
业务事件 id 沿用 outbox 事件的 id，因此同一业务事实重复写入 outbox
（例如重试导致的重复记录）不会推给下游两次。
"""

from __future__ import annotations

from typing import Protocol

from app.application.annotation.ports import OutboxEvent
from app.application.webhook.outbound import OutboundRegistrar
from app.domain.identity.models import ProjectScope


class OutboxEventWriter(Protocol):
    """outbox 仓储协议（包装层只依赖它，不依赖具体 SQLAlchemy 实现）。"""

    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


class OutboundRegisteringOutbox:
    """包装 outbox 仓储：写入业务事件的同时登记对外投递。

    委托全部写入行为给内层仓储，本类只在其上追加一步「登记待投递」。
    """

    def __init__(self, inner: OutboxEventWriter, registrar: OutboundRegistrar) -> None:
        self._inner = inner
        self._registrar = registrar

    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None:
        self._inner.enqueue(scope, event)
        self._registrar.register(
            project_id=scope.project_id,
            event_type=event.event_type,
            event_id=event.event_id,
            payload=event.payload,
            now=event.occurred_at,
        )


__all__ = ["OutboundRegisteringOutbox", "OutboxEventWriter"]
