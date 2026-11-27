"""DDP 告警真实接收端（Alertmanager webhook 接收方）。

包入口：``app.main`` 提供 HTTP 服务，``app.store`` 提供 NDJSON 落盘与查询。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
