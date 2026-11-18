"""导入可观测性：步骤日志消息脱敏（数据源域薄适配）。

规则本体已上移到 :mod:`app.application.observability_redaction`（N4-002
的处理运行阶段日志需要同一套规则，而处理运行域不应 import 数据源域）。
本文件只保留数据源域的默认宽度，既有 ``sanitize_log_message`` 调用点不变。

步骤日志是排障数据，会通过只读端点回给前端，因此**不得携带**密钥、预签名
URL、绝对路径或对象存储地址。
"""

from __future__ import annotations

from app.application.observability_redaction import sanitize_log_message as _sanitize
from app.domain.datasources.models import IMPORT_LOG_MESSAGE_MAX_LENGTH


def sanitize_log_message(message: object, *, limit: int = IMPORT_LOG_MESSAGE_MAX_LENGTH) -> str:
    """数据源导入日志的脱敏入口；默认宽度沿用导入域既有上限。"""
    return _sanitize(message, limit=limit)


__all__ = ["sanitize_log_message"]
