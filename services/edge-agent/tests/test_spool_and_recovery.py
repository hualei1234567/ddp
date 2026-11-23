"""Spool 状态机、SQLite WAL、崩溃恢复与原始副本保护测试（FR-007）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from edge_agent.config import SpoolConfig
from edge_agent.errors import IllegalTransitionError, PurgeNotAllowedError
from edge_agent.spool.manager import SourceFileSpec, SpoolManager
from edge_agent.spool.models import ArtifactReceipt, ServerReceipt, SpoolState
from tests.conftest import FakeClock, make_manifest_context


class _InjectedCrash(RuntimeError):
    pass


def _crash_at(target_point: str):
    def hook(point: str) -> None:
        if point == target_point:
            raise _InjectedCrash(f"crash@{point}")

    return hook


def _write_source(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_spool_wal_mode_happy_lifecycle_and_purge_protection(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    src = _write_source(tmp_path / "export" / "ep01.mcap", b"mcap-binary-payload-1234")
    mgr = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        assert mgr.store.journal_mode() == "wal"

        ctx = make_manifest_context()
        entry = mgr.ingest(
            ctx,
            [
                SourceFileSpec(
                    source_path=src,
                    rel_path="ep01.mcap",
                    mime_type="application/octet-stream",
                    format="MCAP",
                )
            ],
        )
        assert entry.state == SpoolState.PENDING
        assert mgr.payload_path(entry, "ep01.mcap").is_file()
        assert mgr.manifest_path(entry).is_file()
        assert mgr.verify_entry_integrity(entry).manifest_hash == entry.manifest_hash

        # 非法状态转移：PENDING 不能直接跳到 UPLOADED
        with pytest.raises(IllegalTransitionError):
            mgr.store.transition(entry.entry_id, SpoolState.UPLOADED, now=fake_clock.now())

        uploading = mgr.mark_uploading(entry.entry_id)
        assert uploading.state == SpoolState.UPLOADING

        receipt = ServerReceipt(
            received_at=fake_clock.now(),
            artifacts=(
                ArtifactReceipt(
                    artifact_path="ep01.mcap",
                    raw_asset_id="asset-001",
                    sha256=uploading.artifacts[0].sha256,
                    ingestion_status="VERIFIED",
                ),
            ),
        )
        uploaded = mgr.mark_uploaded(entry.entry_id, receipt)
        assert uploaded.state == SpoolState.UPLOADED
        assert uploaded.server_receipt is not None

        # 默认绝不自动删除唯一原始副本
        with pytest.raises(PurgeNotAllowedError):
            mgr.purge(entry.entry_id)
        assert mgr.payload_path(uploaded, "ep01.mcap").is_file()
    finally:
        mgr.close()

    # 显式开启 allow_purge_uploaded 后，仅允许删除已获服务端回执的 UPLOADED 条目
    mgr_purge = SpoolManager(
        SpoolConfig(root=spool_root, allow_purge_uploaded=True), clock=fake_clock
    )
    try:
        mgr_purge.purge(entry.entry_id)
        assert not (spool_root / "uploaded" / entry.entry_id).exists()
        assert src.is_file()  # 原始导出目录源文件也完好无损
    finally:
        mgr_purge.close()


@pytest.mark.parametrize("crash_point", ["after_copy", "after_manifest"])
def test_crash_in_tmp_cleans_tmp_on_recovery_without_touching_source(
    tmp_path: Path, fake_clock: FakeClock, crash_point: str
) -> None:
    spool_root = tmp_path / "spool"
    src = _write_source(tmp_path / "export" / "ep02.mcap", b"payload-crash-in-tmp")
    mgr1 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        with pytest.raises(_InjectedCrash):
            mgr1.ingest(
                make_manifest_context(),
                [
                    SourceFileSpec(
                        source_path=src,
                        rel_path="ep02.mcap",
                        mime_type="application/octet-stream",
                        format="MCAP",
                    )
                ],
                entry_id="entry-tmp-crash",
                crash_hook=_crash_at(crash_point),
            )
    finally:
        mgr1.close()

    # 重启新实例执行恢复
    mgr2 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        report = mgr2.recover()
        assert report.cleaned_tmp_ids == ("entry-tmp-crash",)
        assert not (spool_root / "tmp" / "entry-tmp-crash").exists()
        recovered = mgr2.store.get_entry("entry-tmp-crash")
        assert recovered.state == SpoolState.FAILED
        assert src.read_bytes() == b"payload-crash-in-tmp"
    finally:
        mgr2.close()


def test_crash_after_atomic_rename_promotes_to_pending_on_recovery(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    src = _write_source(tmp_path / "export" / "ep03.mcap", b"payload-after-rename")
    mgr1 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        with pytest.raises(_InjectedCrash):
            mgr1.ingest(
                make_manifest_context(),
                [
                    SourceFileSpec(
                        source_path=src,
                        rel_path="ep03.mcap",
                        mime_type="application/octet-stream",
                        format="MCAP",
                    )
                ],
                entry_id="entry-renamed",
                crash_hook=_crash_at("after_rename_pending"),
            )
    finally:
        mgr1.close()

    mgr2 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        report = mgr2.recover()
        assert report.promoted_pending_ids == ("entry-renamed",)
        recovered = mgr2.store.get_entry("entry-renamed")
        assert recovered.state == SpoolState.PENDING
        assert mgr2.verify_entry_integrity(recovered).manifest_hash == recovered.manifest_hash
    finally:
        mgr2.close()


def test_recovery_preserves_uploading_and_quarantines_corrupt_payload(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    src1 = _write_source(tmp_path / "export" / "ok.mcap", b"valid-uploading-data")
    src2 = _write_source(tmp_path / "export" / "bad.mcap", b"will-be-corrupted")

    mgr1 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        e1 = mgr1.ingest(
            make_manifest_context(),
            [SourceFileSpec(src1, "ok.mcap", "application/octet-stream", "MCAP")],
            entry_id="e-uploading",
        )
        mgr1.mark_uploading(e1.entry_id)

        e2 = mgr1.ingest(
            make_manifest_context(),
            [SourceFileSpec(src2, "bad.mcap", "application/octet-stream", "MCAP")],
            entry_id="e-corrupt",
        )
        # 模拟磁盘静默位翻转
        mgr1.payload_path(e2, "bad.mcap").write_bytes(b"corrupted-bytes!!")
        # 制造孤儿 tmp 目录
        (spool_root / "tmp" / "orphan-dir").mkdir(parents=True)
    finally:
        mgr1.close()

    mgr2 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        report = mgr2.recover()
        assert report.resumable_uploading_ids == ("e-uploading",)
        assert report.quarantined_ids == ("e-corrupt",)
        assert report.orphan_dirs_cleaned == ("orphan-dir",)
        assert mgr2.store.get_entry("e-uploading").state == SpoolState.UPLOADING
        assert mgr2.store.get_entry("e-corrupt").state == SpoolState.QUARANTINED
        assert (spool_root / "quarantine" / "e-corrupt" / "payload" / "bad.mcap").is_file()
    finally:
        mgr2.close()
