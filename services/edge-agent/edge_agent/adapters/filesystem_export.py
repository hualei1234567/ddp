"""Filesystem Export Adapter（FR-005 首批真实能力）。

扫描真实导出目录，产出真实文件清单与元数据；明确声明不支持实时控制/实时采集。
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from edge_agent.adapters.protocol import (
    AdapterCapabilities,
    AdapterHealth,
    DiscoveredArtifact,
    DiscoveredSource,
)
from edge_agent.config import AdapterConfig, ExportSource
from edge_agent.errors import EdgeAgentError, UnsupportedOperationError
from edge_agent.preflight.models import CheckResult, CheckStatus
from edge_agent.timesource import Clock, SystemClock

_EXT_FORMAT_MAP: dict[str, tuple[str, str]] = {
    ".mcap": ("MCAP", "application/octet-stream"),
    ".db3": ("ROSBAG2", "application/x-sqlite3"),
    ".h5": ("HDF5", "application/x-hdf5"),
    ".hdf5": ("HDF5", "application/x-hdf5"),
    ".mp4": ("MP4", "video/mp4"),
    ".parquet": ("PARQUET", "application/vnd.apache.parquet"),
    ".csv": ("CSV", "text/csv"),
    ".json": ("JSON", "application/json"),
    ".zip": ("ZIP", "application/zip"),
}

_SUPPORTED_FORMATS = tuple(sorted({fmt for fmt, _ in _EXT_FORMAT_MAP.values()} | {"UNKNOWN"}))


def _infer_format_and_mime(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in _EXT_FORMAT_MAP:
        return _EXT_FORMAT_MAP[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    mime = guessed.lower() if guessed else "application/octet-stream"
    return "UNKNOWN", mime


class FilesystemExportAdapter:
    """导出目录扫描 Adapter。绝不伪装支持实时控制或实时采集。"""

    ADAPTER_NAME = "filesystem_export"
    ADAPTER_VERSION = "0.1.0"

    def __init__(self, config: AdapterConfig, *, clock: Clock | None = None) -> None:
        self._config = config
        self._clock: Clock = clock or SystemClock()

    @property
    def name(self) -> str:
        return self.ADAPTER_NAME

    @property
    def version(self) -> str:
        return self.ADAPTER_VERSION

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            realtime_control=False,
            realtime_capture=False,
            supports_directory_scan=True,
            supported_formats=_SUPPORTED_FORMATS,
        )

    def start(self) -> None:
        raise UnsupportedOperationError("FilesystemExportAdapter 不支持实时采集启动（start）")

    def stop(self) -> None:
        raise UnsupportedOperationError("FilesystemExportAdapter 不支持实时采集停止（stop）")

    def discover(self) -> list[DiscoveredSource]:
        results: list[DiscoveredSource] = []
        for src in self._config.sources:
            exists = src.root.exists()
            is_dir = src.root.is_dir() if exists else False
            readable = os.access(src.root, os.R_OK | os.X_OK) if is_dir else False
            count = len(self._scan_source(src)) if readable else 0
            results.append(
                DiscoveredSource(
                    name=src.name,
                    root=src.root,
                    exists=exists,
                    is_dir=is_dir,
                    readable=readable,
                    file_count=count,
                )
            )
        return results

    def list_artifacts(self, source_name: str | None = None) -> list[DiscoveredArtifact]:
        targets = self._select_sources(source_name)
        artifacts: list[DiscoveredArtifact] = []
        for src in targets:
            if src.root.is_dir() and os.access(src.root, os.R_OK | os.X_OK):
                artifacts.extend(self._scan_source(src))
        artifacts.sort(key=lambda a: (a.source_name, a.relative_path))
        return artifacts

    def read_metadata(self, artifact: DiscoveredArtifact) -> dict[str, Any]:
        if not artifact.absolute_path.is_file():
            raise EdgeAgentError(f"无法读取不存在的 artifact：{artifact.absolute_path}")
        st = artifact.absolute_path.stat()
        result: dict[str, Any] = {
            "source_name": artifact.source_name,
            "relative_path": artifact.relative_path,
            "size_bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "format": artifact.format,
            "mime_type": artifact.mime_type,
            "stable": artifact.stable,
            "sidecar": {},
        }
        if artifact.sidecar_path is not None and artifact.sidecar_path.is_file():
            try:
                raw = json.loads(artifact.sidecar_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EdgeAgentError(f"sidecar 元数据损坏：{artifact.sidecar_path}：{exc}") from exc
            if not isinstance(raw, dict):
                raise EdgeAgentError(f"sidecar 元数据顶层必须为对象：{artifact.sidecar_path}")
            result["sidecar"] = raw
        return result

    def preflight(self) -> list[CheckResult]:
        if not self._config.sources:
            return [
                CheckResult(
                    check_id="adapter.sources",
                    status=CheckStatus.BLOCKED,
                    message="未配置任何导出目录源",
                    evidence={"source_count": 0},
                )
            ]
        checks: list[CheckResult] = []
        for discovered in self.discover():
            cid = f"adapter.source.{discovered.name}"
            if not discovered.exists or not discovered.is_dir:
                checks.append(
                    CheckResult(
                        check_id=cid,
                        status=CheckStatus.BLOCKED,
                        message=f"导出目录不存在或非目录：{discovered.root}",
                        evidence={"root": str(discovered.root), "exists": discovered.exists},
                    )
                )
            elif not discovered.readable:
                checks.append(
                    CheckResult(
                        check_id=cid,
                        status=CheckStatus.BLOCKED,
                        message=f"导出目录不可读：{discovered.root}",
                        evidence={"root": str(discovered.root), "readable": False},
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        check_id=cid,
                        status=CheckStatus.PASS,
                        message=f"导出目录就绪（发现 {discovered.file_count} 个文件）",
                        evidence={
                            "root": str(discovered.root),
                            "file_count": discovered.file_count,
                        },
                    )
                )
        return checks

    def health_check(self) -> AdapterHealth:
        checks = self.preflight()
        blocked = [c for c in checks if c.status == CheckStatus.BLOCKED]
        if blocked:
            return AdapterHealth(
                healthy=False,
                status=CheckStatus.BLOCKED,
                message="; ".join(c.message for c in blocked),
                details={"blocked_checks": [c.check_id for c in blocked]},
            )
        warned = [c for c in checks if c.status == CheckStatus.WARN]
        if warned:
            return AdapterHealth(
                healthy=True,
                status=CheckStatus.WARN,
                message="; ".join(c.message for c in warned),
                details={"warned_checks": [c.check_id for c in warned]},
            )
        return AdapterHealth(
            healthy=True,
            status=CheckStatus.PASS,
            message=f"FilesystemExportAdapter 正常（{len(checks)} 个源目录就绪）",
            details={"source_count": len(checks)},
        )

    def _select_sources(self, source_name: str | None) -> list[ExportSource]:
        if source_name is None:
            return list(self._config.sources)
        matched = [s for s in self._config.sources if s.name == source_name]
        if not matched:
            raise EdgeAgentError(f"未找到导出源：{source_name}")
        return matched

    def _scan_source(self, src: ExportSource) -> list[DiscoveredArtifact]:
        now_s = self._clock.now().timestamp()
        found: list[DiscoveredArtifact] = []
        for dirpath, dirnames, filenames in os.walk(
            src.root, followlinks=self._config.follow_symlinks
        ):
            dirnames.sort()
            if not self._config.follow_symlinks:
                dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for filename in sorted(filenames):
                full = Path(dirpath) / filename
                if not self._config.follow_symlinks and full.is_symlink():
                    continue
                if self._should_ignore(filename):
                    continue
                st = full.stat()
                fmt, mime = _infer_format_and_mime(full)
                age_s = max(0.0, now_s - st.st_mtime)
                stable = age_s >= self._config.stability_window_s
                sidecar = Path(f"{full}{self._config.sidecar_suffix}")
                rel = full.relative_to(src.root).as_posix()
                found.append(
                    DiscoveredArtifact(
                        source_name=src.name,
                        absolute_path=full,
                        relative_path=rel,
                        size_bytes=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                        mime_type=mime,
                        format=fmt,
                        stable=stable,
                        sidecar_path=sidecar if sidecar.is_file() else None,
                    )
                )
        return found

    def _should_ignore(self, filename: str) -> bool:
        if filename.startswith("."):
            return True
        if filename.endswith(self._config.sidecar_suffix):
            return True
        return any(filename.endswith(suffix) for suffix in self._config.ignored_suffixes)
