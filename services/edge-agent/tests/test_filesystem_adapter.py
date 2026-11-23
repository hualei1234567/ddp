"""Filesystem Export Adapter 测试（FR-005）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from edge_agent.adapters.filesystem_export import FilesystemExportAdapter
from edge_agent.config import AdapterConfig, ExportSource
from edge_agent.errors import UnsupportedOperationError
from edge_agent.preflight.models import CheckStatus
from tests.conftest import FakeClock


def test_filesystem_adapter_declares_no_realtime_capabilities_and_rejects_start_stop(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    adapter = FilesystemExportAdapter(
        AdapterConfig(sources=(ExportSource(name="rig_a", root=export_dir),)),
        clock=fake_clock,
    )
    caps = adapter.capabilities()
    assert caps.realtime_control is False
    assert caps.realtime_capture is False
    assert caps.supports_directory_scan is True

    with pytest.raises(UnsupportedOperationError):
        adapter.start()
    with pytest.raises(UnsupportedOperationError):
        adapter.stop()


def test_filesystem_adapter_discovers_scans_stability_sidecar_and_ignores_temp_files(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    export_dir = tmp_path / "exports"
    sub = export_dir / "session_01"
    sub.mkdir(parents=True)

    stable_file = sub / "episode.mcap"
    stable_file.write_bytes(b"real-mcap-bytes")
    old_ts = fake_clock.now().timestamp() - 120.0
    os.utime(stable_file, (old_ts, old_ts))

    sidecar = Path(f"{stable_file}.meta.json")
    sidecar.write_text(json.dumps({"robot": "franka-01", "fps": 30}), encoding="utf-8")

    fresh_file = sub / "writing.mp4"
    fresh_file.write_bytes(b"mp4-still-writing")
    fresh_ts = fake_clock.now().timestamp() - 2.0
    os.utime(fresh_file, (fresh_ts, fresh_ts))

    # 应被忽略的文件：临时后缀、隐藏文件、符号链接
    (sub / "inflight.mcap.part").write_bytes(b"partial")
    (sub / ".hidden.mcap").write_bytes(b"hidden")
    outside = tmp_path / "outside.mcap"
    outside.write_bytes(b"secret-outside")
    (sub / "link.mcap").symlink_to(outside)

    adapter = FilesystemExportAdapter(
        AdapterConfig(
            sources=(ExportSource(name="rig_a", root=export_dir),),
            stability_window_s=30.0,
            follow_symlinks=False,
        ),
        clock=fake_clock,
    )

    discovered = adapter.discover()
    assert len(discovered) == 1
    assert discovered[0].exists is True
    assert discovered[0].readable is True
    assert discovered[0].file_count == 2

    artifacts = adapter.list_artifacts()
    by_rel = {a.relative_path: a for a in artifacts}
    assert set(by_rel) == {"session_01/episode.mcap", "session_01/writing.mp4"}

    ep = by_rel["session_01/episode.mcap"]
    assert ep.format == "MCAP"
    assert ep.stable is True
    assert ep.sidecar_path == sidecar

    meta = adapter.read_metadata(ep)
    assert meta["sidecar"] == {"robot": "franka-01", "fps": 30}
    assert meta["size_bytes"] == len(b"real-mcap-bytes")

    vid = by_rel["session_01/writing.mp4"]
    assert vid.format == "MP4"
    assert vid.mime_type == "video/mp4"
    assert vid.stable is False

    health = adapter.health_check()
    assert health.healthy is True
    assert health.status == CheckStatus.PASS


def test_filesystem_adapter_preflight_blocks_missing_source_or_empty_config(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    empty_adapter = FilesystemExportAdapter(AdapterConfig(sources=()), clock=fake_clock)
    assert empty_adapter.health_check().status == CheckStatus.BLOCKED

    missing_adapter = FilesystemExportAdapter(
        AdapterConfig(sources=(ExportSource(name="missing", root=tmp_path / "does_not_exist"),)),
        clock=fake_clock,
    )
    checks = missing_adapter.preflight()
    assert checks[0].status == CheckStatus.BLOCKED
    assert missing_adapter.health_check().healthy is False
