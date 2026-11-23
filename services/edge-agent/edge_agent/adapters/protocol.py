"""采集 Adapter 统一协议与数据模型（FR-005）。

文件导出类 Adapter 必须明确声明 ``realtime_control=False``、``realtime_capture=False``，
且调用 ``start()`` / ``stop()`` 时必须显式抛出 ``UnsupportedOperationError``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from edge_agent.preflight.models import CheckResult, CheckStatus


@dataclass(frozen=True)
class AdapterCapabilities:
    realtime_control: bool
    realtime_capture: bool
    supports_directory_scan: bool
    supported_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveredSource:
    name: str
    root: Path
    exists: bool
    is_dir: bool
    readable: bool
    file_count: int = 0


@dataclass(frozen=True)
class DiscoveredArtifact:
    source_name: str
    absolute_path: Path
    relative_path: str
    size_bytes: int
    mtime_ns: int
    mime_type: str
    format: str
    stable: bool
    sidecar_path: Path | None = None


@dataclass(frozen=True)
class AdapterHealth:
    healthy: bool
    status: CheckStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


class CaptureAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def capabilities(self) -> AdapterCapabilities: ...

    def discover(self) -> list[DiscoveredSource]: ...

    def preflight(self) -> list[CheckResult]: ...

    def list_artifacts(self, source_name: str | None = None) -> list[DiscoveredArtifact]: ...

    def read_metadata(self, artifact: DiscoveredArtifact) -> dict[str, Any]: ...

    def health_check(self) -> AdapterHealth: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...
