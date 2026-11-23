"""Preflight 检查引擎与启动门禁（FR-004）。

强制红线：
- 逐项输出 PASS / WARN / BLOCKED（覆盖 11 项检查维度）。
- 存在任一 BLOCKED 时禁止启动采集，不可被普通继续标志绕过。
- WARN 仅在显式确认且策略允许时可继续，并产出可审计的 ProceedEvidence。
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import InvalidVersion, Version

from edge_agent.adapters.protocol import CaptureAdapter
from edge_agent.config import PreflightThresholds
from edge_agent.errors import PreflightAckError, PreflightBlockedError
from edge_agent.preflight.models import CheckResult, CheckStatus, PreflightReport, ProceedEvidence
from edge_agent.spool.models import SpoolResidueSummary
from edge_agent.spool.store import SpoolStore
from edge_agent.timesource import Clock, SystemClock

_ALLOWED_TASK_STATUSES = frozenset({"ACTIVE", "IN_PROGRESS", "SCHEDULED", "READY"})
_ALLOWED_SESSION_STATUSES = frozenset({"READY", "SCHEDULED", "ACTIVE", "CAPTURING"})


@dataclass(frozen=True)
class PreflightInput:
    """Preflight 11 项检查的输入快照（纯数据，由调用方或运行时采集）。"""

    identity_valid: bool
    agent_version: str
    task_status: str | None
    session_status: str | None
    lease_valid: bool
    devices_available: Mapping[str, bool] = field(default_factory=dict)
    sensors_available: Mapping[str, bool] = field(default_factory=dict)
    calibration_snapshot_id: str | None = None
    calibration_valid: bool = True
    calibration_expired: bool = False
    adapter: CaptureAdapter | None = None
    spool_root: Path | None = None
    disk_free_bytes: int | None = None
    clock_skew_ms: int | None = None
    network_reachable: bool = True
    network_latency_ms: int | None = None
    spool_writable: bool | None = None
    residue: SpoolResidueSummary | None = None
    identity_reason: str | None = None
    lease_reason: str | None = None


class PreflightEngine:
    """执行 11 项 Preflight 检查并强制实施 BLOCKED / WARN 启动门禁。"""

    def __init__(self, thresholds: PreflightThresholds, *, clock: Clock | None = None) -> None:
        self._thresholds = thresholds
        self._clock: Clock = clock or SystemClock()

    def run(
        self,
        capture_session_id: str,
        inp: PreflightInput,
        *,
        spool_store: SpoolStore | None = None,
    ) -> PreflightReport:
        residue = inp.residue
        if residue is None and spool_store is not None:
            residue = spool_store.residue_for_other_sessions(capture_session_id)

        results: list[CheckResult] = [
            self._check_identity(inp),
            self._check_version(inp),
            self._check_task_session(inp),
            self._check_devices_sensors(inp),
            self._check_calibration(inp),
            self._check_adapter(inp),
            self._check_disk_space(inp),
            self._check_clock_skew(inp),
            self._check_network(inp),
            self._check_spool_writable(inp),
            self._check_residue(residue),
        ]
        return PreflightReport(
            capture_session_id=capture_session_id,
            generated_at=self._clock.now(),
            results=tuple(results),
        )

    def authorize_start(
        self,
        report: PreflightReport,
        *,
        allow_warn: bool = False,
        acknowledged_by: str | None = None,
        acknowledged_check_ids: Sequence[str] = (),
        persist_store: SpoolStore | None = None,
    ) -> ProceedEvidence | None:
        """启动门禁：BLOCKED 绝对不可绕过；WARN 需策略允许 + 完整确认并记录证据。"""
        if report.overall == CheckStatus.BLOCKED:
            raise PreflightBlockedError(report.ids_with(CheckStatus.BLOCKED))

        if report.overall == CheckStatus.PASS:
            return None

        warned_ids = report.ids_with(CheckStatus.WARN)
        if not allow_warn:
            raise PreflightAckError(f"任务策略不允许带 WARN 启动：{', '.join(warned_ids)}")
        if not acknowledged_by or not acknowledged_by.strip():
            raise PreflightAckError("带 WARN 继续必须提供确认人 acknowledged_by")
        acked_set = set(acknowledged_check_ids)
        missing = [cid for cid in warned_ids if cid not in acked_set]
        if missing:
            raise PreflightAckError(f"以下 WARN 项尚未确认：{', '.join(missing)}")

        evidence = ProceedEvidence(
            evidence_id=str(uuid.uuid4()),
            capture_session_id=report.capture_session_id,
            decided_at=self._clock.now(),
            acknowledged_by=acknowledged_by.strip(),
            acknowledged_check_ids=tuple(sorted(acked_set)),
            warned_results=tuple(r for r in report.results if r.status == CheckStatus.WARN),
            report_hash=report.report_hash(),
        )
        if persist_store is not None:
            persist_store.save_preflight_evidence(evidence)
        return evidence

    def _check_identity(self, inp: PreflightInput) -> CheckResult:
        if inp.identity_valid:
            return CheckResult("identity", CheckStatus.PASS, "Agent 身份有效")
        return CheckResult(
            "identity",
            CheckStatus.BLOCKED,
            inp.identity_reason or "Agent 身份无效或已撤销",
            {"identity_valid": False},
        )

    def _check_version(self, inp: PreflightInput) -> CheckResult:
        try:
            current = Version(inp.agent_version)
        except InvalidVersion:
            return CheckResult(
                "agent_version",
                CheckStatus.BLOCKED,
                f"Agent 版本号非法：{inp.agent_version}",
                {"agent_version": inp.agent_version},
            )
        minimum = Version(self._thresholds.min_agent_version)
        if current < minimum:
            return CheckResult(
                "agent_version",
                CheckStatus.BLOCKED,
                f"Agent 版本 {current} 低于最低要求 {minimum}",
                {"current": str(current), "min_required": str(minimum)},
            )
        rec_raw = self._thresholds.recommended_agent_version
        if rec_raw is not None and current < Version(rec_raw):
            return CheckResult(
                "agent_version",
                CheckStatus.WARN,
                f"Agent 版本 {current} 低于推荐版本 {rec_raw}",
                {"current": str(current), "recommended": rec_raw},
            )
        return CheckResult(
            "agent_version",
            CheckStatus.PASS,
            f"Agent 版本 {current} 满足要求",
            {"current": str(current)},
        )

    def _check_task_session(self, inp: PreflightInput) -> CheckResult:
        if not inp.lease_valid:
            return CheckResult(
                "task_session",
                CheckStatus.BLOCKED,
                inp.lease_reason or "CaptureSession Lease 无效或已过期",
                {"lease_valid": False},
            )
        session_ok = (inp.session_status or "").upper() in _ALLOWED_SESSION_STATUSES
        task_ok = inp.task_status is None or inp.task_status.upper() in _ALLOWED_TASK_STATUSES
        if not session_ok or not task_ok:
            msg = (
                f"任务或 Session 状态不允许采集"
                f"（task={inp.task_status}, session={inp.session_status}）"
            )
            return CheckResult(
                "task_session",
                CheckStatus.BLOCKED,
                msg,
                {"task_status": inp.task_status, "session_status": inp.session_status},
            )
        return CheckResult(
            "task_session",
            CheckStatus.PASS,
            "任务、Session 状态与 Lease 有效",
            {"task_status": inp.task_status, "session_status": inp.session_status},
        )

    def _check_devices_sensors(self, inp: PreflightInput) -> CheckResult:
        down_devices = sorted(k for k, ok in inp.devices_available.items() if not ok)
        down_sensors = sorted(k for k, ok in inp.sensors_available.items() if not ok)
        if down_devices or down_sensors:
            return CheckResult(
                "devices_sensors",
                CheckStatus.BLOCKED,
                f"存在不可用设备或传感器（devices={down_devices}, sensors={down_sensors}）",
                {"unavailable_devices": down_devices, "unavailable_sensors": down_sensors},
            )
        if not inp.devices_available and not inp.sensors_available:
            return CheckResult(
                "devices_sensors",
                CheckStatus.WARN,
                "未绑定任何设备或传感器清单",
                {"device_count": 0, "sensor_count": 0},
            )
        return CheckResult(
            "devices_sensors",
            CheckStatus.PASS,
            "所有绑定设备与传感器可用",
            {
                "device_count": len(inp.devices_available),
                "sensor_count": len(inp.sensors_available),
            },
        )

    def _check_calibration(self, inp: PreflightInput) -> CheckResult:
        if not inp.calibration_snapshot_id or not inp.calibration_valid:
            return CheckResult(
                "calibration",
                CheckStatus.BLOCKED,
                "缺少有效标定快照",
                {"calibration_snapshot_id": inp.calibration_snapshot_id},
            )
        if inp.calibration_expired:
            return CheckResult(
                "calibration",
                CheckStatus.WARN,
                f"标定快照 {inp.calibration_snapshot_id} 已过期，建议重新标定",
                {"calibration_snapshot_id": inp.calibration_snapshot_id, "expired": True},
            )
        return CheckResult(
            "calibration",
            CheckStatus.PASS,
            f"标定快照 {inp.calibration_snapshot_id} 有效",
            {"calibration_snapshot_id": inp.calibration_snapshot_id},
        )

    def _check_adapter(self, inp: PreflightInput) -> CheckResult:
        if inp.adapter is None:
            return CheckResult("adapter_health", CheckStatus.BLOCKED, "未配置采集 Adapter")
        health = inp.adapter.health_check()
        return CheckResult(
            "adapter_health",
            health.status,
            health.message,
            {"adapter": inp.adapter.name, "version": inp.adapter.version, **dict(health.details)},
        )

    def _check_disk_space(self, inp: PreflightInput) -> CheckResult:
        free = inp.disk_free_bytes
        if free is None and inp.spool_root is not None:
            probe_dir = inp.spool_root if inp.spool_root.exists() else inp.spool_root.parent
            free = shutil.disk_usage(probe_dir).free
        if free is None:
            return CheckResult("disk_space", CheckStatus.BLOCKED, "无法获取 Spool 磁盘余量")
        if free < self._thresholds.min_free_bytes_block:
            return CheckResult(
                "disk_space",
                CheckStatus.BLOCKED,
                f"磁盘余量不足（{free} < {self._thresholds.min_free_bytes_block} bytes）",
                {"free_bytes": free, "block_threshold": self._thresholds.min_free_bytes_block},
            )
        if free < self._thresholds.min_free_bytes_warn:
            return CheckResult(
                "disk_space",
                CheckStatus.WARN,
                f"磁盘余量偏低（{free} < {self._thresholds.min_free_bytes_warn} bytes）",
                {"free_bytes": free, "warn_threshold": self._thresholds.min_free_bytes_warn},
            )
        return CheckResult("disk_space", CheckStatus.PASS, "磁盘余量充足", {"free_bytes": free})

    def _check_clock_skew(self, inp: PreflightInput) -> CheckResult:
        if inp.clock_skew_ms is None:
            return CheckResult(
                "clock_skew",
                self._thresholds.clock_unavailable_status,
                "无法检测时钟偏移",
                {"clock_skew_ms": None},
            )
        skew = abs(inp.clock_skew_ms)
        if skew >= self._thresholds.clock_skew_block_ms:
            return CheckResult(
                "clock_skew",
                CheckStatus.BLOCKED,
                f"时钟偏移过大（{skew}ms >= {self._thresholds.clock_skew_block_ms}ms）",
                {"clock_skew_ms": inp.clock_skew_ms},
            )
        if skew >= self._thresholds.clock_skew_warn_ms:
            return CheckResult(
                "clock_skew",
                CheckStatus.WARN,
                f"时钟存在偏移（{skew}ms >= {self._thresholds.clock_skew_warn_ms}ms）",
                {"clock_skew_ms": inp.clock_skew_ms},
            )
        return CheckResult(
            "clock_skew",
            CheckStatus.PASS,
            f"时钟同步正常（偏移 {skew}ms）",
            {"clock_skew_ms": skew},
        )

    def _check_network(self, inp: PreflightInput) -> CheckResult:
        if not inp.network_reachable:
            return CheckResult(
                "network",
                self._thresholds.network_unreachable_status,
                "服务端网络不可达",
                {"reachable": False},
            )
        return CheckResult(
            "network",
            CheckStatus.PASS,
            "服务端网络可达",
            {"reachable": True, "latency_ms": inp.network_latency_ms},
        )

    def _check_spool_writable(self, inp: PreflightInput) -> CheckResult:
        writable = inp.spool_writable
        if writable is None:
            writable = _probe_spool_writable(inp.spool_root)
        if not writable:
            return CheckResult(
                "spool_writable",
                CheckStatus.BLOCKED,
                f"Spool 目录不可写：{inp.spool_root}",
                {"spool_root": str(inp.spool_root) if inp.spool_root else None},
            )
        return CheckResult(
            "spool_writable",
            CheckStatus.PASS,
            "Spool 目录可写",
            {"spool_root": str(inp.spool_root) if inp.spool_root else None},
        )

    def _check_residue(self, residue: SpoolResidueSummary | None) -> CheckResult:
        if residue is None or residue.entry_count == 0:
            return CheckResult(
                "previous_session_residue", CheckStatus.PASS, "无上一 Session 残留数据"
            )
        msg = (
            f"检测到 {residue.entry_count} 条未上传的上一 Session 残留数据"
            f"（{residue.total_bytes} bytes）"
        )
        return CheckResult(
            "previous_session_residue",
            self._thresholds.residue_status,
            msg,
            {
                "entry_count": residue.entry_count,
                "total_bytes": residue.total_bytes,
                "session_ids": list(residue.session_ids),
            },
        )


def _probe_spool_writable(spool_root: Path | None) -> bool:
    if spool_root is None:
        return False
    probe_dir = spool_root / "tmp"
    probe_file = probe_dir / f".preflight_probe_{uuid.uuid4().hex}"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        with probe_file.open("wb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe_file.unlink()
        return True
    except OSError:
        return False
