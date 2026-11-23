"""Manifest 输入模型（设计 §8 字段全集）。

不含任何 token / 凭证，也不含 agent 自报的 verified 字段：校验结论只能由服务端给出。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CalibrationRef:
    calibration_id: str
    version: str


@dataclass(frozen=True)
class ClockInfo:
    """时钟元数据。``sync`` 为自由结构（整数/字符串/布尔），如 offset_us、method。"""

    source: str  # 例如 ntp / ptp / system / unknown
    unit: str  # ns / us / ms / s
    sync: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamInfo:
    stream_id: str
    kind: str
    sensor_id: str | None = None
    artifact_paths: Sequence[str] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactInfo:
    path: str  # Spool 条目 payload 内的 POSIX 相对路径
    mime_type: str
    size_bytes: int
    sha256: str  # 十六进制或 "sha256:<hex>"
    format: str  # MCAP / ROSBAG2 / HDF5 / MP4 / PARQUET / CSV / JSON / ZIP / UNKNOWN
    duration_ms: int | None = None


@dataclass(frozen=True)
class ManifestWarning:
    code: str
    message: str
    artifact_path: str | None = None


@dataclass(frozen=True)
class ManifestContext:
    """除 artifacts / warnings 之外的全部 Manifest 上下文。"""

    agent_id: str
    agent_version: str
    capture_session_id: str
    project_id: str
    clock: ClockInfo
    adapter_name: str
    adapter_version: str
    collection_task_id: str | None = None
    robot_id: str | None = None
    device_ids: Sequence[str] = ()
    sensor_ids: Sequence[str] = ()
    calibrations: Sequence[CalibrationRef] = ()
    start_time: datetime | None = None
    end_time: datetime | None = None
    streams: Sequence[StreamInfo] = ()


@dataclass(frozen=True)
class Manifest:
    """已构建的 Manifest：规范化文档 + canonical 字节 + manifest hash。"""

    document: Mapping[str, Any]
    canonical_bytes: bytes
    manifest_hash: str
