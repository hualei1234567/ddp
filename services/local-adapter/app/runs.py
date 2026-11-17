"""进程内运行登记与并发上限。

单进程内存队列只用于本地参考实现：状态与回调记录在进程重启后丢失。
并发上限防止无界线程/内存增长；容量满时 submit 返回 429，不排队到无限。
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

RUN_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "REJECTED"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RunRecord:
    run_id: str
    kind: str
    status: str = "QUEUED"
    adapter_id: str = ""
    adapter_version: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    project_id: str | None = None
    dataset_version_id: str | None = None
    export_job_id: str | None = None
    export_prefix: str | None = None
    correlation_id: str | None = None
    delivery_id: str | None = None
    evaluation_id: str | None = None
    task_id: str | None = None
    model_version_id: str | None = None
    service_id: str | None = None
    episode_ids: list[str] = field(default_factory=list)
    config: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, object] = field(default_factory=dict)
    resolution: dict[str, object] = field(default_factory=dict)
    callback_events: list[dict[str, object]] = field(default_factory=list)
    cancel_requested: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "project_id": self.project_id,
            "dataset_version_id": self.dataset_version_id,
            "export_job_id": self.export_job_id,
            "export_prefix": self.export_prefix,
            "correlation_id": self.correlation_id,
            "delivery_id": self.delivery_id,
            "evaluation_id": self.evaluation_id,
            "task_id": self.task_id,
            "model_version_id": self.model_version_id,
            "service_id": self.service_id,
            "episode_ids": list(self.episode_ids),
            "config": dict(self.config),
            "error_code": self.error_code,
            "error_summary": self.error_summary,
            "metrics": dict(self.metrics),
            "artifacts": dict(self.artifacts),
            "resolution": dict(self.resolution),
            "callback_events": [dict(item) for item in self.callback_events],
            "cancel_requested": self.cancel_requested,
        }


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._order: deque[str] = deque()
        self._lock = threading.Lock()
        self._max_records = 500

    def create(self, record: RunRecord) -> RunRecord:
        with self._lock:
            self._runs[record.run_id] = record
            self._order.append(record.run_id)
            while len(self._order) > self._max_records:
                oldest = self._order.popleft()
                existing = self._runs.get(oldest)
                if existing is not None and existing.status in RUN_TERMINAL:
                    self._runs.pop(oldest, None)
                else:
                    self._order.appendleft(oldest)
                    break
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        record = self.get(run_id)
        return None if record is None else record.to_dict()

    def update(self, run_id: str, **fields: object) -> RunRecord | None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            for key, value in fields.items():
                if key in {"run_id", "kind", "created_at", "cancel_event"}:
                    continue
                if not hasattr(record, key):
                    continue
                setattr(record, key, value)
            record.updated_at = now_iso()
            return record

    def append_callback(self, run_id: str, entry: dict[str, object]) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                record.callback_events.append(entry)
                record.updated_at = now_iso()

    def request_cancel(self, run_id: str) -> tuple[bool, RunRecord | None]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False, None
            if record.status in RUN_TERMINAL:
                return False, record
            record.cancel_requested = True
            record.cancel_event.set()
            record.updated_at = now_iso()
            return True, record

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            run_ids = list(self._order)[-limit:]
        items = []
        for run_id in run_ids:
            snapshot = self.snapshot(run_id)
            if snapshot is not None:
                items.append(snapshot)
        return items

    def counts(self) -> dict[str, int]:
        with self._lock:
            queued = sum(1 for item in self._runs.values() if item.status == "QUEUED")
            running = sum(1 for item in self._runs.values() if item.status == "RUNNING")
            return {"queued": queued, "running": running, "records": len(self._runs)}


class JobRunner:
    def __init__(self, *, max_workers: int, max_pending: int) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ddp-local-adapter"
        )
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._in_flight = 0
        self._closed = False

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def submit(self, run_id: str, fn: Callable[[], None]) -> bool:
        with self._lock:
            if self._closed or self._in_flight >= self._max_pending:
                return False
            self._in_flight += 1
        future = self._executor.submit(self._guard, run_id, fn)
        future.add_done_callback(self._done)
        return True

    def _guard(self, run_id: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # 任务函数内部已收敛；这里只防线程静默死亡
            import logging

            logging.getLogger("ddp.local_adapter.runner").exception(
                "后台任务未捕获异常 run=%s", run_id
            )

    def _done(self, _future: object) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["JobRunner", "RUN_TERMINAL", "RunRecord", "RunRegistry", "now_iso"]
