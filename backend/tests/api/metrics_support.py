"""指标测试的日志采集器（``/metrics`` 采集正确性与访问控制两组用例共用）。

夹具在 ``conftest.py``；这里只放辅助类。单独成模块是为了让两个测试文件
共享同一份实现，而不必互相导入（互相导入会让收集顺序影响结果）。
"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter


class CollectingHandler(logging.Handler):
    """挂到具名 logger 上的采集器（``create_app()`` 会清空 root handlers）。

    **必须在 emit 时就格式化**：``trace_id`` 来自 contextvar，``RequestIdMiddleware``
    在请求结束时复位它，事后再格式化只能读到 ``None``。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.payloads: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))

    def messages(self) -> list[str]:
        return [str(payload.get("message", "")) for payload in self.payloads]
