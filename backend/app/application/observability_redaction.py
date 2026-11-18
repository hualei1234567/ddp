"""跨域共享的日志脱敏入口（薄适配）。

规则本体在 :mod:`app.core.logging`（TD-24 / N2-008 收敛为单一实现）：日志
格式化与导入/处理步骤日志必须用同一套凭据规则，避免两处正则漂移。本模块只
保留 ``application`` 层既有的导入路径与默认列宽，调用点不变。

步骤日志/失败摘要既会落库，也会通过只读端点回给前端，因此**不得携带**
密钥、预签名 URL、绝对路径或对象存储地址。异常文本（``str(exc)``）不受
我们控制，可能带上游 URL 或本机路径，所以统一在写入前与返回前各脱敏一次
（纵深防御）。
"""

from __future__ import annotations

from app.core.logging import DEFAULT_LOG_MESSAGE_MAX_LENGTH
from app.core.logging import sanitize_log_message as _sanitize


def sanitize_log_message(
    message: object,
    *,
    limit: int = DEFAULT_LOG_MESSAGE_MAX_LENGTH,
) -> str:
    """对外回显文本的脱敏入口；规则与列宽默认值均来自 ``app.core.logging``。"""
    return _sanitize(message, limit=limit)


__all__ = ["DEFAULT_LOG_MESSAGE_MAX_LENGTH", "sanitize_log_message"]
