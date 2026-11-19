"""MCAP 只读元数据 Adapter（DDP-V21-N3-005）。

**样例来源**：全部由官方 ``mcap`` 1.6.0 的 writer API 在测试运行时生成，
属于合成数据，不包含任何客户/生产数据，也不下载外部文件。

覆盖：

- 正常多 channel / 多 schema：topic、编码、schema 关联、每 channel 消息数与
  时间范围可核对；
- 合法空文件按 0 消息报告，不当作失败；
- 有 summary 但无 statistics 时，消息计数显式为 ``None``（未知，不是 0）；
- 完全没有 summary 索引的合法 MCAP 显式返回 ``has_summary=False``；
- 非 MCAP、截断、损坏记录长度、缺文件、SDK 不可用；
- channel/schema 数、读取字节、文本长度上限显式失败；
- 只读 footer/summary，不读取 chunk 数据页、不调用消息迭代；
- 错误信息不带绝对路径，返回摘要不含消息体。
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from ddp_data.adapters import build_default_registry
from ddp_data.mcap import (
    DEFAULT_MCAP_ADAPTER,
    MAX_CHANNELS,
    MAX_METADATA_BYTES,
    MAX_SCHEMAS,
    McapInputError,
    McapMetadataError,
    McapMetadataLimitError,
    McapUnavailableError,
    NotMcapError,
    detect_mcap,
    read_mcap_metadata,
    read_mcap_metadata_from,
    summarize_mcap,
)
from ddp_data.mcap import metadata as metadata_module
from mcap.reader import SeekingReader
from mcap.writer import CompressionType, IndexType, Writer

_MESSAGE_BYTES = b"payload-bytes-marker"
_HEADER_LENGTH_OFFSET = 9  # 8 字节魔数 + 1 字节 record opcode
_FOOTER_SUMMARY_START = slice(-28, -20)  # 文件尾 8 字节魔数前，Footer 的 summary_start


class _CountingReader:
    """记录合计读取字节数；用于证明不读取 chunk 数据页。"""

    def __init__(self, handle: object) -> None:
        self._handle = handle
        self.bytes_read = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)  # type: ignore[attr-defined]

    def tell(self) -> int:
        return self._handle.tell()  # type: ignore[attr-defined]

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)  # type: ignore[attr-defined]
        self.bytes_read += len(data)
        return data

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self._handle.closed  # type: ignore[attr-defined]


def _build_mcap(
    path: Path,
    *,
    empty: bool = False,
    statistics: bool = True,
    with_summary: bool = True,
    repeat_details: bool = True,
    payload_size: int = len(_MESSAGE_BYTES),
) -> Path:
    options: dict[str, object] = {"compression": CompressionType.NONE}
    if not repeat_details:
        options.update(repeat_channels=False, repeat_schemas=False)
    if not with_summary:
        options.update(
            index_types=IndexType.NONE,
            use_statistics=False,
            use_summary_offsets=False,
            repeat_channels=False,
            repeat_schemas=False,
        )
    elif not statistics:
        options.update(
            index_types=IndexType.NONE,
            use_statistics=False,
            use_summary_offsets=False,
        )
    with path.open("wb") as stream:
        writer = Writer(stream, **options)
        writer.start(profile="robot/teleop", library="ddp-test")
        if not empty:
            camera_schema = writer.register_schema(
                name="pkg/CameraFrame", encoding="jsonschema", data=b'{"type":"object"}'
            )
            imu_schema = writer.register_schema(
                name="pkg/Imu", encoding="protobuf", data=b"\x0a\x03abc"
            )
            front = writer.register_channel(
                topic="/camera/front", message_encoding="json", schema_id=camera_schema
            )
            inertial = writer.register_channel(
                topic="/imu", message_encoding="protobuf", schema_id=imu_schema
            )
            logs = writer.register_channel(topic="/log", message_encoding="json", schema_id=0)
            payload = _MESSAGE_BYTES + b"x" * max(0, payload_size - len(_MESSAGE_BYTES))
            for channel_id, log_time in (
                (front, 1_000_000_000),
                (inertial, 1_200_000_000),
                (front, 1_500_000_000),
                (logs, 1_700_000_000),
            ):
                writer.add_message(
                    channel_id=channel_id,
                    log_time=log_time,
                    data=payload,
                    publish_time=log_time,
                )
        writer.finish()
    return path


# --- 正常样例：channel/schema/消息计数 -------------------------------------


def test_detect_requires_both_magic_marks(tmp_path: Path) -> None:
    raw = _build_mcap(tmp_path / "sample.mcap").read_bytes()

    assert detect_mcap(raw) is True
    assert detect_mcap(raw[:40]) is False
    assert detect_mcap(b"not an mcap file") is False
    assert detect_mcap(raw[:-8] + b"XXXXXXXX") is False


def test_multichannel_metadata_is_bounded_and_explicit(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "multi.mcap"))

    assert metadata.profile == "robot/teleop"
    assert metadata.library == "ddp-test"
    assert metadata.has_summary is True
    assert metadata.channel_count == 3
    assert metadata.schema_count == 2
    assert metadata.message_count == 4
    assert metadata.message_start_time_ns == 1_000_000_000
    assert metadata.message_end_time_ns == 1_700_000_000

    channels = {channel.topic: channel for channel in metadata.channels}
    assert isinstance(channels["/camera/front"].message_count, int)
    assert channels["/camera/front"].message_count == 2
    assert channels["/camera/front"].message_encoding == "json"
    assert channels["/camera/front"].schema_name == "pkg/CameraFrame"
    assert channels["/camera/front"].schema_encoding == "jsonschema"
    assert channels["/imu"].message_encoding == "protobuf"
    assert channels["/imu"].schema_name == "pkg/Imu"
    # schema_id == 0 表示不引用 schema；不能伪造 schema 名称。
    assert channels["/log"].schema_id == 0
    assert channels["/log"].schema_name is None
    assert channels["/log"].schema_encoding is None
    assert [channel.channel_id for channel in metadata.channels] == sorted(
        channel.channel_id for channel in metadata.channels
    )

    schemas = {schema.name: schema for schema in metadata.schemas}
    assert schemas["pkg/CameraFrame"].encoding == "jsonschema"
    assert schemas["pkg/CameraFrame"].data_bytes == len(b'{"type":"object"}')


def test_empty_mcap_is_zero_messages_not_failure(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "empty.mcap", empty=True))

    assert metadata.has_summary is True
    assert metadata.channel_count == 0
    assert metadata.schema_count == 0
    assert metadata.message_count == 0
    assert metadata.message_start_time_ns is None
    assert metadata.message_end_time_ns is None
    assert metadata.channels == ()
    assert metadata.schemas == ()


def test_summary_without_statistics_reports_unknown_counts(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "nostats.mcap", statistics=False))

    assert metadata.has_summary is True
    assert metadata.channel_count == 3
    assert metadata.schema_count == 2
    # 没有 statistics 时消息计数与时间范围是未知，不是 0。
    assert metadata.message_count is None
    assert metadata.message_start_time_ns is None
    assert metadata.message_end_time_ns is None
    assert all(channel.message_count is None for channel in metadata.channels)


def test_summary_without_repeated_details_still_reports_counts(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "nodetails.mcap", repeat_details=False))

    assert metadata.has_summary is True
    # 数量来自 statistics；明细未收录，因此 channels/schemas 为空。
    assert metadata.channel_count == 3
    assert metadata.schema_count == 2
    assert metadata.message_count == 4
    assert metadata.channels == ()
    assert metadata.schemas == ()


def test_mcap_without_summary_is_explicitly_unknown(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "nosummary.mcap", with_summary=False))

    assert metadata.has_summary is False
    assert metadata.channel_count is None
    assert metadata.schema_count is None
    assert metadata.message_count is None
    assert metadata.channels == ()
    assert metadata.schemas == ()
    assert metadata.profile == "robot/teleop"


def test_as_metadata_is_bounded_and_serializable(tmp_path: Path) -> None:
    metadata = read_mcap_metadata(_build_mcap(tmp_path / "serialize.mcap"))
    payload = metadata.as_metadata()
    text = json.dumps(payload)

    assert str(tmp_path) not in text
    assert "pkg/CameraFrame" in text
    assert _MESSAGE_BYTES.decode() not in text
    assert payload["message_count"] == 4
    assert len(payload["channels"]) == 3


def test_adapter_facade_keeps_stable_identity(tmp_path: Path) -> None:
    path = _build_mcap(tmp_path / "facade.mcap")

    assert DEFAULT_MCAP_ADAPTER.adapter_id == "mcap.metadata_readonly"
    assert DEFAULT_MCAP_ADAPTER.version == "1.0.0"
    assert DEFAULT_MCAP_ADAPTER.detect(path.read_bytes()) is True
    assert DEFAULT_MCAP_ADAPTER.read_metadata(path).message_count == 4
    assert DEFAULT_MCAP_ADAPTER.inspect(path).channel_count == 3
    assert summarize_mcap(path).schema_count == 2


def test_not_registered_as_episode_adapter() -> None:
    registry = build_default_registry()

    assert all("mcap" not in spec.adapter_id for spec in registry.list_specs())


# --- 输入与损坏：全部显式失败 -----------------------------------------------


def test_missing_file_fails_without_leaking_path(tmp_path: Path) -> None:
    missing = tmp_path / "private-dataset.mcap"
    with pytest.raises(McapInputError) as excinfo:
        read_mcap_metadata(missing)
    assert excinfo.value.code == "invalid_input"
    assert str(tmp_path) not in str(excinfo.value)


def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(McapInputError):
        read_mcap_metadata(tmp_path)


@pytest.mark.parametrize("payload", [b"", b"\x89MCAP0\r\n", b"not an mcap file at all"])
def test_non_mcap_and_truncated_inputs_fail(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "input.bin"
    path.write_bytes(payload)
    with pytest.raises(NotMcapError):
        read_mcap_metadata(path)


def test_truncated_mcap_fails_with_not_mcap(tmp_path: Path) -> None:
    raw = _build_mcap(tmp_path / "full.mcap").read_bytes()
    truncated = tmp_path / "truncated.mcap"
    truncated.write_bytes(raw[:-10])
    with pytest.raises(NotMcapError):
        read_mcap_metadata(truncated)


def test_corrupt_summary_offset_fails_explicitly(tmp_path: Path) -> None:
    raw = bytearray(_build_mcap(tmp_path / "whole.mcap").read_bytes())
    # summary_start 指向文件之外：footer 本身仍可读，但 summary 区间不可读。
    raw[_FOOTER_SUMMARY_START] = struct.pack("<Q", len(raw) + 4096)
    path = tmp_path / "corrupt.mcap"
    path.write_bytes(bytes(raw))

    with pytest.raises(McapMetadataError) as excinfo:
        read_mcap_metadata(path)
    assert excinfo.value.code == "invalid_metadata"
    assert str(tmp_path) not in str(excinfo.value)


def test_record_length_limit_fails_explicitly(tmp_path: Path) -> None:
    raw = bytearray(_build_mcap(tmp_path / "whole2.mcap").read_bytes())
    raw[_HEADER_LENGTH_OFFSET : _HEADER_LENGTH_OFFSET + 8] = struct.pack(
        "<Q", MAX_METADATA_BYTES + 1
    )
    path = tmp_path / "huge-record.mcap"
    path.write_bytes(bytes(raw))

    with pytest.raises(McapMetadataLimitError) as excinfo:
        read_mcap_metadata(path)
    assert excinfo.value.code == "metadata_limit_exceeded"


def test_missing_sdk_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _build_mcap(tmp_path / "sdk.mcap")
    monkeypatch.setattr(metadata_module, "_mcap_reader", None)

    with pytest.raises(McapUnavailableError) as excinfo:
        read_mcap_metadata(path)
    assert excinfo.value.code == "mcap_unavailable"


# --- 有界：数量、字节与文本 -------------------------------------------------


def test_channel_count_limit_fails_explicitly(tmp_path: Path) -> None:
    path = _build_mcap(tmp_path / "channels.mcap")
    with pytest.raises(McapMetadataLimitError):
        read_mcap_metadata(path, max_channels=2)
    # 默认上限足够，确认不是永远失败。
    assert read_mcap_metadata(path, max_channels=MAX_CHANNELS).channel_count == 3


def test_schema_count_limit_fails_explicitly(tmp_path: Path) -> None:
    path = _build_mcap(tmp_path / "schemas.mcap")
    with pytest.raises(McapMetadataLimitError):
        read_mcap_metadata(path, max_schemas=MAX_SCHEMAS - 4095)


def test_byte_limit_fails_before_returning_partial_metadata(tmp_path: Path) -> None:
    path = _build_mcap(tmp_path / "bytes.mcap")
    with pytest.raises(McapMetadataLimitError):
        read_mcap_metadata(path, max_metadata_bytes=32)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_metadata_bytes": 0},
        {"max_channels": 0},
        {"max_schemas": -1},
        {"max_channels": True},
    ],
)
def test_invalid_limits_are_rejected(tmp_path: Path, limits: dict[str, object]) -> None:
    path = _build_mcap(tmp_path / "limits.mcap")
    with pytest.raises(McapInputError):
        read_mcap_metadata(path, **limits)  # type: ignore[arg-type]


# --- 只读：不读 chunk、不解码消息 -------------------------------------------


def test_only_footer_and_summary_are_read(tmp_path: Path) -> None:
    big = 4 * 1024 * 1024
    path = _build_mcap(tmp_path / "big.mcap", payload_size=big)
    size = path.stat().st_size
    assert size > big

    with path.open("rb", buffering=0) as raw_handle:
        counter = _CountingReader(raw_handle)
        metadata = read_mcap_metadata_from(counter, size=size)

    assert metadata.message_count == 4
    # 只读取 header/footer/summary，读取量远小于 4 MiB 消息数据。
    assert counter.bytes_read < 64 * 1024
    assert counter.bytes_read < size // 100


def test_message_decoding_is_never_invoked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _build_mcap(tmp_path / "nodecode.mcap")

    def _must_not_iterate(*_args: object, **_kwargs: object) -> None:
        pytest.fail("只读元数据不应调用消息迭代/解码")

    monkeypatch.setattr(SeekingReader, "iter_messages", _must_not_iterate)
    monkeypatch.setattr(SeekingReader, "iter_decoded_messages", _must_not_iterate)

    assert read_mcap_metadata(path).channel_count == 3
