"""共享测试夹具：确定性时钟与标准 Manifest 上下文工厂。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from edge_agent.manifest.models import CalibrationRef, ClockInfo, ManifestContext


class FakeClock:
    """确定性测试时钟：支持推进时间并记录 sleep 调用。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
        self._mono = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


def make_manifest_context(**overrides: object) -> ManifestContext:
    defaults: dict[str, object] = {
        "agent_id": "agent-workstation-01",
        "agent_version": "0.1.0",
        "capture_session_id": str(uuid.uuid4()),
        "collection_task_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "robot_id": str(uuid.uuid4()),
        "device_ids": [str(uuid.uuid4())],
        "sensor_ids": [str(uuid.uuid4())],
        "calibrations": [CalibrationRef(calibration_id=str(uuid.uuid4()), version="v1")],
        "start_time": datetime(2026, 11, 23, 11, 55, 0, tzinfo=UTC),
        "end_time": datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC),
        "clock": ClockInfo(source="ptp", unit="ns", sync={"offset_us": 12, "locked": True}),
        "adapter_name": "filesystem_export",
        "adapter_version": "0.1.0",
    }
    defaults.update(overrides)
    return ManifestContext(**defaults)  # type: ignore[arg-type]
