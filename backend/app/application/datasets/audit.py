"""数据集审计与事务性 outbox 编排适配层。

规则本体在 :mod:`app.application.audit`（唯一权威实现）；本文件只做重新导出，
保持各域既有的 ``from app.application.<domain>.audit import record_change`` 调用点。
"""

from __future__ import annotations

from app.application.audit import AuditRecord, OutboxEvent, record_change

__all__ = ["AuditRecord", "OutboxEvent", "record_change"]
