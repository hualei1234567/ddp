"""阶段 5 审计与 outbox 记录适配层。

规则本体在 :mod:`app.application.audit`（唯一权威实现）。本域历史上用了
``AuditRecordData`` / ``OutboxEventData`` 两个别名，这里保留以免改动调用点。
"""

from __future__ import annotations

from app.application.audit import AuditRecord, OutboxEvent, record_change
from app.application.modelops.ports import UnitOfWork

AuditRecordData = AuditRecord
OutboxEventData = OutboxEvent

__all__ = [
    "AuditRecord",
    "AuditRecordData",
    "OutboxEvent",
    "OutboxEventData",
    "UnitOfWork",
    "record_change",
]
