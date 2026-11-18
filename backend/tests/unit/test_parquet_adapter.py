"""Parquet 只读 Adapter（DDP-V21-N3-003）。

**样例来源**：``tests/fixtures/parquet/*.parquet`` 由 PyArrow 23.0.1
（``pq.write_table(..., compression="none", store_schema=False)``）真实写出，
属于合成数据，不包含任何客户/生产数据。仓库运行时不把 PyArrow 放进 backend
requirements，而是由 ``data-platform/requirements.txt`` 提供（CI 后端 job 显式安装）。

覆盖：

- 真实写入器产出的 schema/行数/行组数可解析，列名与类型可解释；
- 逻辑类型（STRING/DECIMAL/TIME/TIMESTAMP/INTEGER）映射到可核对文本；
- LeRobot 表布局识别与 ``missing_fields`` 显式声明；
- 通用表不凭空声明期望字段；
- 空表按 0 行报告，不当作失败；
- 非 Parquet、损坏 footer、加密 footer、非法 metadata 长度；
- “只读 footer、不整文件读入内存”，以及错误消息不带路径或文件内容。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from ddp_data.parquet import (
    DEFAULT_PARQUET_ADAPTER,
    MAX_METADATA_BYTES,
    ExpectedColumn,
    NotParquetError,
    ParquetEncryptedError,
    ParquetInputError,
    ParquetLayout,
    ParquetMetadataError,
    ParquetMetadataLimitError,
    detect_parquet,
    read_parquet_metadata,
    read_parquet_metadata_from,
    summarize_parquet,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "parquet"
_LEROBOT = _FIXTURES / "lerobot_v21_data.parquet"
_GENERIC = _FIXTURES / "generic_types.parquet"
_LOGICAL = _FIXTURES / "logical_types.parquet"
_EMPTY = _FIXTURES / "empty_table.parquet"


class _CountingReader:
    """记录合计读取字节数；用于证明不整文件读入内存。"""

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

    def readinto(self, buffer: object) -> int:
        read = self._handle.readinto(buffer)  # type: ignore[attr-defined]
        self.bytes_read += read
        return read

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self._handle.closed  # type: ignore[attr-defined]


def _replace_metadata_payload(source: Path, target: Path, payload: bytes) -> Path:
    """替换 footer 元数据字节，保留长度与首尾魔数。"""
    raw = source.read_bytes()
    metadata_length = struct.unpack("<I", raw[-8:-4])[0]
    if len(payload) != metadata_length:
        raise AssertionError("替换载荷长度必须与原 footer 一致")
    start = len(raw) - 8 - metadata_length
    target.write_bytes(raw[:start] + payload + raw[-8:])
    return target


def _write_container(
    path: Path,
    payload: bytes,
    *,
    header: bytes = b"PAR1",
    trailer_magic: bytes = b"PAR1",
    declared_length: int | None = None,
) -> Path:
    length = len(payload) if declared_length is None else declared_length
    path.write_bytes(header + payload + struct.pack("<I", length) + trailer_magic)
    return path


# --- 真实样例：schema、计数与类型 -------------------------------------------


def test_detect_requires_both_magic_marks() -> None:
    raw = _LEROBOT.read_bytes()
    assert detect_parquet(raw) is True
    assert detect_parquet(raw[:40]) is False
    assert detect_parquet(b"not a parquet file at all") is False
    assert detect_parquet(raw[:-4] + b"PARE") is True


def test_lerobot_sample_schema_and_counts() -> None:
    metadata = read_parquet_metadata(_LEROBOT)

    assert metadata.format_version == "2.6"
    assert metadata.num_rows == 3
    assert metadata.row_group_count == 1
    assert metadata.created_by is not None
    assert "parquet-cpp-arrow" in metadata.created_by
    assert metadata.top_level_fields == (
        "episode_index",
        "frame_index",
        "timestamp",
        "observation.state",
        "action",
        "task_index",
        "index",
    )

    state_element = metadata.column_by_path("observation.state.list.element")
    assert state_element is not None
    assert state_element.physical_type == "FLOAT"
    assert state_element.repetition == "REPEATED"
    assert metadata.column_by_path("episode_index").physical_type == "INT64"
    assert metadata.column_by_path("episode_index").repetition == "OPTIONAL"


def test_lerobot_layout_declares_missing_fields_explicitly() -> None:
    preview = summarize_parquet(_LEROBOT)

    assert preview.adapter_id == "parquet.table_readonly"
    assert preview.adapter_version == "1.0.0"
    assert preview.layout is ParquetLayout.LEROBOT_TABULAR
    assert preview.key_columns["episode_index"] == "episode_index"
    assert preview.key_columns["observation.state"] == "observation.state"
    assert preview.key_columns["next.done"] is None
    assert preview.missing_fields == frozenset({"next.done", "next.reward"})
    assert preview.missing_required_fields == frozenset()
    assert preview.unmapped_columns == frozenset()


def test_generic_table_does_not_invent_expected_columns() -> None:
    preview = summarize_parquet(_GENERIC)

    assert preview.layout is ParquetLayout.GENERIC_TABLE
    assert preview.num_rows == 2
    # 未声明期望时为空表示“没有期望”，不是“字段齐全”。
    assert preview.key_columns == {}
    assert preview.missing_fields == frozenset()
    assert preview.unmapped_columns == frozenset(preview.top_level_fields)
    assert "meta" in preview.top_level_fields


def test_logical_and_nested_types_are_explainable() -> None:
    metadata = read_parquet_metadata(_GENERIC)
    columns = {column.path: column for column in metadata.columns}

    label = columns["label"]
    assert label.physical_type == "BYTE_ARRAY"
    assert label.logical_type == "STRING"
    assert label.converted_type == "UTF8"

    amount = columns["amount"]
    assert amount.physical_type == "FIXED_LEN_BYTE_ARRAY"
    assert amount.logical_type == "DECIMAL(scale=2, precision=9)"
    assert amount.converted_type == "DECIMAL"

    created = columns["created"]
    assert created.logical_type == "TIMESTAMP(unit=MICROS, utc=True)"
    assert created.converted_type == "TIMESTAMP_MICROS"

    day = columns["day"]
    assert day.logical_type == "DATE" and day.converted_type == "DATE"

    # 嵌套 struct 的叶子路径采用 Parquet 规范点分路径。
    assert columns["meta.a"].physical_type == "INT64"
    assert columns["meta.b"].logical_type == "STRING"


def test_integer_and_time_logical_types_are_explainable() -> None:
    metadata = read_parquet_metadata(_LOGICAL)
    columns = {column.path: column for column in metadata.columns}

    assert columns["u8"].physical_type == "INT32"
    assert columns["u8"].logical_type == "INTEGER(bitWidth=8, signed=False)"
    assert columns["u8"].converted_type == "UINT_8"
    assert columns["i16"].logical_type == "INTEGER(bitWidth=16, signed=True)"
    assert columns["i16"].converted_type == "INT_16"
    assert columns["t32"].logical_type == "TIME(unit=MILLIS, utc=False)"
    assert columns["t32"].converted_type is None
    assert columns["t64"].logical_type == "TIME(unit=MICROS, utc=False)"
    assert columns["t64"].converted_type is None


def test_empty_table_is_zero_rows_not_failure() -> None:
    preview = summarize_parquet(_EMPTY)

    assert preview.num_rows == 0
    assert preview.row_group_count == 1
    assert preview.columns  # schema 仍然存在
    # 缺少 frame_index/timestamp 时不能冒充 LeRobot 布局。
    assert preview.layout is ParquetLayout.GENERIC_TABLE


def test_expected_columns_override_is_respected() -> None:
    expected = (
        ExpectedColumn("label", "文本标签", required=True),
        ExpectedColumn("action", "动作向量", required=True),
    )
    preview = summarize_parquet(_GENERIC, expected_columns=expected)

    assert preview.key_columns == {"label": "label", "action": None}
    assert preview.missing_fields == frozenset({"action"})
    assert preview.missing_required_fields == frozenset({"action"})
    assert "count" in preview.unmapped_columns
    assert "label" not in preview.unmapped_columns


def test_duplicate_expected_columns_are_rejected() -> None:
    with pytest.raises(ValueError, match="不能重复"):
        summarize_parquet(
            _GENERIC,
            expected_columns=(ExpectedColumn("label", "a"), ExpectedColumn("label", "b")),
        )


def test_adapter_facade_keeps_stable_identity() -> None:
    assert DEFAULT_PARQUET_ADAPTER.detect(_LEROBOT.read_bytes()) is True
    preview = DEFAULT_PARQUET_ADAPTER.inspect(_LEROBOT)
    assert preview.adapter_id == DEFAULT_PARQUET_ADAPTER.adapter_id
    metadata = DEFAULT_PARQUET_ADAPTER.read_metadata(_LEROBOT)
    assert metadata.num_rows == 3


# --- 输入与损坏：全部显式失败 -----------------------------------------------


def test_missing_file_fails_without_leaking_path(tmp_path: Path) -> None:
    secret = "very-secret-dataset-name.parquet"
    with pytest.raises(ParquetInputError) as excinfo:
        read_parquet_metadata(tmp_path / secret)
    assert secret not in str(excinfo.value)


def test_non_parquet_and_truncated_inputs_fail(tmp_path: Path) -> None:
    text = tmp_path / "text.bin"
    text.write_bytes(b"not a parquet file at all")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(text)

    short = tmp_path / "short.bin"
    short.write_bytes(b"PAR1")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(short)

    bad_header = tmp_path / "header.bin"
    bad_header.write_bytes(b"XXXX" + _LEROBOT.read_bytes()[4:])
    with pytest.raises(NotParquetError):
        read_parquet_metadata(bad_header)

    bad_tail = tmp_path / "tail.bin"
    bad_tail.write_bytes(_LEROBOT.read_bytes()[:-4] + b"XXXX")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(bad_tail)


def test_encrypted_footer_is_rejected(tmp_path: Path) -> None:
    raw = bytearray(_LEROBOT.read_bytes())
    raw[-4:] = b"PARE"
    path = tmp_path / "encrypted.parquet"
    path.write_bytes(bytes(raw))
    with pytest.raises(ParquetEncryptedError):
        read_parquet_metadata(path)


def test_metadata_length_limit_fails_before_parsing(tmp_path: Path) -> None:
    path = _write_container(
        tmp_path / "too-big.parquet",
        b"",
        declared_length=MAX_METADATA_BYTES + 1,
    )
    with pytest.raises(ParquetMetadataLimitError):
        read_parquet_metadata(path)


def test_corrupt_metadata_bytes_fail_explicitly(tmp_path: Path) -> None:
    raw = _LEROBOT.read_bytes()
    metadata_length = struct.unpack("<I", raw[-8:-4])[0]
    zeroed = _replace_metadata_payload(
        _LEROBOT, tmp_path / "zeroed.parquet", bytes(metadata_length)
    )
    with pytest.raises(ParquetMetadataError):
        read_parquet_metadata(zeroed)


def test_errors_do_not_echo_file_content_or_path(tmp_path: Path) -> None:
    raw = _LEROBOT.read_bytes()
    metadata_length = struct.unpack("<I", raw[-8:-4])[0]
    marker = b"SECRET-CONTENT-MARKER"
    payload = marker + bytes(metadata_length - len(marker))
    path = _replace_metadata_payload(_LEROBOT, tmp_path / "content.parquet", payload)

    with pytest.raises(ParquetMetadataError) as excinfo:
        read_parquet_metadata(path)
    message = str(excinfo.value)
    assert "SECRET-CONTENT-MARKER" not in message
    assert str(tmp_path) not in message


# --- 有界读取：只碰 footer ---------------------------------------------------


def test_only_footer_region_is_read_not_whole_file(tmp_path: Path) -> None:
    raw = _LEROBOT.read_bytes()
    # 在文件头与 footer 之间插入 8 MiB“数据页”，读取量不应随之增长。
    bloated = raw[:4] + b"\x00" * (8 * 1024 * 1024) + raw[4:]
    path = tmp_path / "bloated.parquet"
    path.write_bytes(bloated)

    with path.open("rb", buffering=0) as raw_handle:
        counter = _CountingReader(raw_handle)
        metadata = read_parquet_metadata_from(counter, size=len(bloated))

    assert metadata.num_rows == 3
    # PyArrow 读取 footer 时会按块预读（本机实测 64 KiB），但远小于整文件。
    assert counter.bytes_read <= 128 * 1024
    assert counter.bytes_read < len(bloated) // 100
