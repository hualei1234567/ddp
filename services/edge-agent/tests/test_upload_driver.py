"""可续传分片上传驱动与故障注入测试（FR-007 / FR-008）。

使用显式测试替身 ``tests/fake_gateway.py``（InMemoryServerGateway），验证：
- 多分片上传与 SQLite 游标持久化
- 崩溃重启续传（跳过已 ACK 分片，复用 UploadSession）
- 断网临时故障指数退避重试
- 预签名 URL 过期/临近过期重签（不重建 UploadSession）
- 重复 ACK / complete 幂等（不资产分叉）
- ETag / Receipt SHA256 / 本地篡改触发 QUARANTINED 隔离
- UploadSession 失效重建
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edge_agent.config import AgentConfig, SpoolConfig, UploadConfig
from edge_agent.errors import ChecksumMismatchError, GatewayTransientError, SpoolIntegrityError
from edge_agent.spool.manager import SourceFileSpec, SpoolManager
from edge_agent.spool.models import SpoolState
from edge_agent.upload.driver import UploadDriver
from tests.conftest import FakeClock, make_manifest_context
from tests.fake_gateway import InMemoryServerGateway


class _InjectedCrash(RuntimeError):
    pass


def _ingest_sample(
    mgr: SpoolManager, tmp_path: Path, payload: bytes, name: str = "episode.mcap"
) -> str:
    src = tmp_path / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(payload)
    entry = mgr.ingest(
        make_manifest_context(),
        [SourceFileSpec(src, name, "application/octet-stream", "MCAP")],
    )
    return entry.entry_id


def test_multipart_upload_happy_path_and_sqlite_persistence(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=256)
    try:
        eid = _ingest_sample(mgr, tmp_path, b"A" * 700)  # 3 parts: 256, 256, 188
        driver = UploadDriver(mgr, gw, UploadConfig(backoff_base_s=0.1), clock=fake_clock)
        receipt = driver.upload_entry(eid)

        assert len(receipt.artifacts) == 1
        assert receipt.artifacts[0].raw_asset_id
        assert gw.create_session_calls == 1
        assert gw.upload_part_calls == [1, 2, 3]
        assert gw.ack_part_calls == [1, 2, 3]

        entry = mgr.store.get_entry(eid)
        assert entry.state == SpoolState.UPLOADED
        cursor = mgr.store.get_upload_cursor(eid, "episode.mcap")
        assert cursor is not None
        assert cursor.raw_asset_id == receipt.artifacts[0].raw_asset_id
        assert len(cursor.parts) == 3
        assert all(p.acked and p.etag for p in cursor.parts)
    finally:
        mgr.close()


def test_crash_mid_upload_resumes_without_reuploading_acked_parts(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=200)

    def crash_after_part_1_ack(point: str) -> None:
        if point == "after_part_ack:1":
            raise _InjectedCrash("killed after part 1 ack")

    mgr1 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        eid = _ingest_sample(mgr1, tmp_path, b"B" * 550)  # 3 parts
        driver1 = UploadDriver(
            mgr1, gw, UploadConfig(), clock=fake_clock, crash_hook=crash_after_part_1_ack
        )
        with pytest.raises(_InjectedCrash):
            driver1.upload_entry(eid)
        first_cursor = mgr1.store.get_upload_cursor(eid, "episode.mcap")
        assert first_cursor is not None
        first_sid = first_cursor.upload_session_id
    finally:
        mgr1.close()

    # 进程重启：新 SpoolManager + recover() + 新 UploadDriver
    mgr2 = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        report = mgr2.recover()
        assert report.resumable_uploading_ids == (eid,)

        driver2 = UploadDriver(mgr2, gw, UploadConfig(), clock=fake_clock)
        receipt = driver2.upload_entry(eid)
        assert receipt.artifacts[0].raw_asset_id
        assert gw.create_session_calls == 1  # 未重建 UploadSession
        assert gw.upload_part_calls == [1, 2, 3]  # 分片 1 未重复上传！
        cursor = mgr2.store.get_upload_cursor(eid, "episode.mcap")
        assert cursor is not None and cursor.upload_session_id == first_sid
        assert mgr2.store.get_entry(eid).state == SpoolState.UPLOADED
    finally:
        mgr2.close()


def test_url_expiry_and_near_expiry_resigns_without_recreating_session(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=300)
    gw.expire_url_on_put_once.add(1)
    gw.return_nearly_expired_url_once.add(2)
    try:
        eid = _ingest_sample(mgr, tmp_path, b"C" * 500)  # 2 parts
        driver = UploadDriver(mgr, gw, UploadConfig(url_refresh_margin_s=30.0), clock=fake_clock)
        driver.upload_entry(eid)

        assert gw.create_session_calls == 1  # 绝不重建 UploadSession
        assert gw.get_part_url_calls == 4  # part1 重签一次 + part2 临近过期重签一次
        assert mgr.store.get_entry(eid).state == SpoolState.UPLOADED
    finally:
        mgr.close()


def test_transient_network_outage_retries_with_backoff_and_recovers(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=512)
    gw.transient_failures_left["upload_part"] = 2
    gw.transient_failures_left["ack_part"] = 1
    try:
        eid = _ingest_sample(mgr, tmp_path, b"D" * 400)
        driver = UploadDriver(
            mgr,
            gw,
            UploadConfig(max_attempts=5, backoff_base_s=1.0, backoff_max_s=10.0),
            clock=fake_clock,
        )
        driver.upload_entry(eid)
        assert mgr.store.get_entry(eid).state == SpoolState.UPLOADED
        assert fake_clock.sleeps == [1.0, 2.0, 1.0]
    finally:
        mgr.close()


def test_transient_exhaustion_keeps_uploading_and_idempotent_replay_no_bifurcation(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=512)
    try:
        eid = _ingest_sample(mgr, tmp_path, b"E" * 300)
        gw.transient_failures_left["complete_session"] = 2
        driver = UploadDriver(
            mgr, gw, UploadConfig(max_attempts=2, backoff_base_s=0.5), clock=fake_clock
        )
        with pytest.raises(GatewayTransientError):
            driver.upload_entry(eid)
        assert mgr.store.get_entry(eid).state == SpoolState.UPLOADING

        # 再次执行续传：complete 成功
        receipt1 = driver.upload_entry(eid)
        assert mgr.store.get_entry(eid).state == SpoolState.UPLOADED

        # 显式重放 complete_session 与 ack_part：幂等且不资产分叉
        cursor = mgr.store.get_upload_cursor(eid, "episode.mcap")
        assert cursor is not None
        p1 = cursor.parts[0]
        ack_replay = gw.ack_part(
            cursor.upload_session_id,
            p1.part_number,
            etag=p1.etag or "",
            size_bytes=p1.size_bytes,
            sha256=p1.sha256,
        )
        assert ack_replay.etag == p1.etag

        from edge_agent.upload.gateway import CompletePartSpec, CompleteSessionRequest

        complete_replay = gw.complete_session(
            CompleteSessionRequest(
                upload_session_id=cursor.upload_session_id,
                expected_version=cursor.session_version,
                expected_sha256=mgr.store.get_entry(eid).artifacts[0].sha256,
                expected_size_bytes=300,
                parts=(CompletePartSpec(1, p1.etag or "", p1.size_bytes, p1.sha256),),
                idempotency_key=f"complete:{eid}:episode.mcap",
            )
        )
        assert complete_replay.replayed is True
        assert complete_replay.raw_asset_id == receipt1.artifacts[0].raw_asset_id
    finally:
        mgr.close()


@pytest.mark.parametrize("tamper_mode", ["etag", "receipt_sha256", "local_file"])
def test_tampering_or_checksum_mismatch_quarantines_entry(
    tmp_path: Path, fake_clock: FakeClock, tamper_mode: str
) -> None:
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=256)
    try:
        eid = _ingest_sample(mgr, tmp_path, b"F" * 300)
        if tamper_mode == "etag":
            gw.tamper_etag_on_part.add(1)
            expected_exc: type[Exception] = ChecksumMismatchError
        elif tamper_mode == "receipt_sha256":
            gw.tamper_receipt_sha256 = True
            expected_exc = ChecksumMismatchError
        else:
            entry = mgr.store.get_entry(eid)
            mgr.payload_path(entry, "episode.mcap").write_bytes(b"X" * 300)
            expected_exc = SpoolIntegrityError

        driver = UploadDriver(mgr, gw, UploadConfig(), clock=fake_clock)
        with pytest.raises(expected_exc):
            driver.upload_entry(eid)
        assert mgr.store.get_entry(eid).state == SpoolState.QUARANTINED
        assert (tmp_path / "spool" / "quarantine" / eid / "payload" / "episode.mcap").is_file()
    finally:
        mgr.close()


def test_session_gone_recreates_session_within_limit(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    spool_root = tmp_path / "spool"
    gw = InMemoryServerGateway(fake_clock, part_size_bytes=200)
    mgr = SpoolManager(SpoolConfig(root=spool_root), clock=fake_clock)
    try:
        eid = _ingest_sample(mgr, tmp_path, b"G" * 350)
        # 先跑一次在 part 1 后崩溃，再把服务端该 Session 标记为 gone
        driver1 = UploadDriver(
            mgr,
            gw,
            UploadConfig(),
            clock=fake_clock,
            crash_hook=lambda p: (_ for _ in ()).throw(_InjectedCrash())
            if p == "after_part_ack:1"
            else None,
        )
        with pytest.raises(_InjectedCrash):
            driver1.upload_entry(eid)
        old_cursor = mgr.store.get_upload_cursor(eid, "episode.mcap")
        assert old_cursor is not None
        gw.gone_session_ids.add(old_cursor.upload_session_id)

        driver2 = UploadDriver(mgr, gw, UploadConfig(max_session_recreations=1), clock=fake_clock)
        driver2.upload_entry(eid)
        assert gw.create_session_calls == 2
        assert mgr.store.get_entry(eid).state == SpoolState.UPLOADED
    finally:
        mgr.close()


def test_agent_config_toml_roundtrip(tmp_path: Path) -> None:
    cfg_file = tmp_path / "agent.toml"
    cfg_file.write_text(
        f"""
agent_id = "edge-01"

[spool]
root = "{(tmp_path / 'spool').as_posix()}"
allow_purge_uploaded = false

[adapter]
stability_window_s = 15.0
[[adapter.sources]]
name = "rig"
root = "{(tmp_path / 'exports').as_posix()}"

[upload]
max_attempts = 4
url_refresh_margin_s = 20.0

[preflight]
min_agent_version = "0.1.0"
clock_skew_warn_ms = 50
clock_skew_block_ms = 500
""",
        encoding="utf-8",
    )
    cfg = AgentConfig.from_toml(cfg_file)
    assert cfg.agent_id == "edge-01"
    assert cfg.spool.allow_purge_uploaded is False
    assert cfg.adapter.sources[0].name == "rig"
    assert cfg.upload.max_attempts == 4
    assert cfg.preflight.clock_skew_warn_ms == 50
