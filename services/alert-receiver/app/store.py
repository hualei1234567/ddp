"""告警落盘与查询（NDJSON 追加写）。

设计要点（对应审计项「告警未接真实 Alertmanager 实例」）：

1. **落盘为准**：收到告警立刻追加写 ``alerts.ndjson``（每条一行），
   查询接口**从文件读回**而不是从内存读。这样进程重启后仍可查询——
   「留下证据」是本服务的核心目的。
2. **多进程安全**：用 ``fcntl.flock`` 排他锁包住「追加 + flush」，
   避免多个进程/线程同时写导致行交错（NDJSON 一旦半行写入，
   后续所有查询都会解析失败）。
3. **只记告警本身**：不记录任何凭据、请求头、来源 IP。
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: 落盘文件名（固定，便于运维直接 tail / grep）
ALERTS_FILENAME = "alerts.ndjson"

#: 默认落盘目录（compose 里挂命名卷到此处）
DEFAULT_DATA_DIR = "/var/lib/ddp-alerts"

#: 单行 JSON 的最大字节数。超过说明 body 有问题，直接跳过而不是
#: 让一个畸形行把整个文件变成不可解析。
MAX_LINE_BYTES = 4 * 1024 * 1024


class AlertStore:
    """告警 NDJSON 存储。

    :param data_dir: 落盘目录；目录不存在时自动创建。
    """

    def __init__(self, data_dir: str | os.PathLike[str] | None = None) -> None:
        self._dir = Path(data_dir or DEFAULT_DATA_DIR)
        self._path = self._dir / ALERTS_FILENAME
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """落盘文件路径。"""
        return self._path

    @property
    def data_dir(self) -> Path:
        """落盘目录路径。"""
        return self._dir

    @contextmanager
    def _locked(self) -> Iterator[Any]:
        """打开文件并持有排他锁（跨进程安全）。"""
        # mode="a"：只追加写，绝不截断既有证据。
        with self._path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(self, record: dict[str, Any]) -> None:
        """追加一条告警记录（每条一行 JSON）。

        :param record: 已解析的告警对象（Alertmanager webhook payload 或其子集）。
        :raises ValueError: 记录无法序列化成单行 JSON。
        """
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if "\n" in line:  # pragma: no cover - json.dumps 不会产生裸换行
            raise ValueError("序列化结果含换行，不能作为 NDJSON 单行写入")
        payload = line + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise ValueError(f"告警记录超过单行上限（{MAX_LINE_BYTES} 字节）")
        with self._locked() as handle:
            # 注意：必须用 handle.write，不能用 Path.write_text —— 后者以 "w" 模式
            # 打开，会**截断**既有证据文件。告警是「证据」，只允许追加。
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        """读回全部告警（跳过无法解析的行，不让一行坏数据毁掉全部可查性）。"""
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    # 坏行（可能被并发写坏）跳过：宁可少一条，也要把其余证据保住。
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
        return records

    def read_recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        """按时间倒序返回最近的告警。

        :param limit: 最多返回条数；``None`` 表示不限。
        """
        records = self.read_all()
        records.reverse()  # 文件是追加写的，最新在最后；倒序即「最新在前」
        if limit is not None and limit >= 0:
            return records[:limit]
        return records


def _alertname_of(record: dict[str, Any]) -> str | None:
    """从一条落盘记录里取 alertname。

    兼容两种形态：
    - Alertmanager webhook 顶层结构：``commonLabels.alertname``
    - 单条告警结构：``labels.alertname``
    """
    common = record.get("commonLabels")
    if isinstance(common, dict):
        name = common.get("alertname")
        if isinstance(name, str) and name:
            return name
    labels = record.get("labels")
    if isinstance(labels, dict):
        name = labels.get("alertname")
        if isinstance(name, str) and name:
            return name
    return None


def _status_of(record: dict[str, Any]) -> str | None:
    """从一条落盘记录里取告警状态（firing / resolved）。"""
    status = record.get("status")
    if isinstance(status, str) and status:
        return status
    alerts = record.get("alerts")
    if isinstance(alerts, list):
        for item in alerts:
            if isinstance(item, dict):
                item_status = item.get("status")
                if isinstance(item_status, str) and item_status:
                    return item_status
    return None


def build_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """统计计数：总数 / firing / resolved / 按 alertname 分组。

    :param records: 落盘记录列表（顺序不影响统计结果）。
    """
    by_name: dict[str, int] = {}
    firing = 0
    resolved = 0
    unknown = 0
    for record in records:
        name = _alertname_of(record)
        key = name or "(unknown)"
        by_name[key] = by_name.get(key, 0) + 1
        status = _status_of(record)
        if status == "firing":
            firing += 1
        elif status == "resolved":
            resolved += 1
        else:
            unknown += 1
    return {
        "total": len(records),
        "firing": firing,
        "resolved": resolved,
        "unknown_status": unknown,
        "by_alertname": dict(sorted(by_name.items())),
    }
