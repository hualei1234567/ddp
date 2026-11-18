"""Parquet 只读 Adapter（DDP-V21-N3-003）。

**样例来源**：``tests/fixtures/parquet/*.parquet`` 由 PyArrow 17.0.0
（``pq.write_table(..., compression="none", store_schema=False)``）真实写出，
属于合成数据，不包含任何客户/生产数据。仓库运行时不依赖 PyArrow，因此样例以
二进制形式提交，测试只读取它们。

覆盖：

- 真实写入器产出的 schema/行数/行组数可解析，列名与类型可解释；
- LeRobot 表布局识别与 ``missing_fields`` 显式声明；
- 通用表不凭空声明期望字段；
- 空表按 0 行报告，不当作失败；
- 非 Parquet、损坏 footer、加密 footer、未知物理类型、schema 自相矛盾；
- footer 长度上限，以及“只读 footer、不整文件读入内存”；
- 错误消息不带路径或文件内容。
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
_EMPTY = _FIXTURES / "empty_table.parquet"

# --- 最小 Thrift Compact 写入器：只用于构造定向损坏的元数据 ----------------
_T_STRUCT = 12
_T_I32 = 5
_T_I64 = 6
_T_BINARY = 8
_T_LIST = 9


def _varint(value: int) -> bytes:
    assert value >= 0
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _zigzag(value: int) -> bytes:
    return _varint((value << 1) ^ (value >> 63))


def _binary(raw: bytes) -> bytes:
    return _varint(len(raw)) + raw


def _struct(fields: list[tuple[int, int, bytes]]) -> bytes:
    out = bytearray()
    last = 0
    for field_id, type_id, payload in fields:
        delta = field_id - last
        if 0 < delta <= 15:
            out.append((delta << 4) | type_id)
        else:
            out.append(type_id)
            out += _zigzag(field_id)
        out += payload
        last = field_id
    out.append(0)
    return bytes(out)


def _list_header(type_id: int, size: int) -> bytes:
    if size < 15:
        return bytes([(size << 4) | type_id])
    return bytes([0xF0 | type_id]) + _varint(size)


def _schema_element(
    name: str,
    *,
    physical_type: int | None = None,
    repetition: int | None = 1,
    num_children: int | None = None,
) -> bytes:
    fields: list[tuple[int, int, bytes]] = []
    if physical_type is not None:
        fields.append((1, _T_I32, _zigzag(physical_type)))
    if repetition is not None:
        fields.append((3, _T_I32, _zigzag(repetition)))
    fields.append((4, _T_BINARY, _binary(name.encode())))
    if num_children is not None:
        fields.append((5, _T_I32, _zigzag(num_children)))
    return _struct(fields)


def _file_metadata(
    elements: list[bytes],
    *,
    version: int = 2,
    num_rows: int = 1,
    include_num_rows: bool = True,
) -> bytes:
    fields: list[tuple[int, int, bytes]] = [(1, _T_I32, _zigzag(version))]
    fields.append((2, _T_LIST, _list_header(_T_STRUCT, len(elements)) + b"".join(elements)))
    if include_num_rows:
        fields.append((3, _T_I64, _zigzag(num_rows)))
    fields.append((4, _T_LIST, _list_header(_T_STRUCT, 0)))
    return _struct(fields)


def _write_container(
    path: Path,
    payload: bytes,
    *,
    header: bytes = b"PAR1",
    trailer_magic: bytes = b"PAR1",
    declared_length: int | None = None,
    filler: bytes = b"",
) -> Path:
    length = len(payload) if declared_length is None else declared_length
    path.write_bytes(header + filler + payload + struct.pack("<I", length) + trailer_magic)
    return path


class _CountingReader:
    """记录合计读取字节数；用于证明不整文件读入内存。"""

    def __init__(self, handle: object) -> None:
        self._handle = handle
        self.bytes_read = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)  # type: ignore[attr-defined]

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)  # type: ignore[attr-defined]
        self.bytes_read += len(data)
        return data


# --- 真实样例：schema、计数与类型 -------------------------------------------


def test_detect_requires_both_magic_marks() -> None:
    raw = _LEROBOT.read_bytes()
    assert detect_parquet(raw) is True
    assert detect_parquet(raw[:40]) is False
    assert detect_parquet(b"not a parquet file at all") is False
    assert detect_parquet(raw[:-4] + b"PARE") is True


def test_lerobot_sample_schema_and_counts() -> None:
    metadata = read_parquet_metadata(_LEROBOT)

    assert metadata.version == 2
    assert metadata.num_rows == 3
    assert metadata.row_group_count == 1
    assert metadata.root_name == "schema"
    assert metadata.created_by is not None
    assert "parquet-cpp-arrow" in metadata.created_by
    assert [field.name for field in metadata.top_level_fields] == [
        "episode_index",
        "frame_index",
        "timestamp",
        "observation.state",
        "action",
        "task_index",
        "index",
    ]
    state = metadata.field_by_path(("observation.state",))
    assert state is not None and not state.is_leaf and state.num_children == 1
    leaves = {"/".join(field.path): field for field in metadata.leaf_columns}
    assert leaves["observation.state/list/element"].physical_type == "FLOAT"
    assert leaves["episode_index"].physical_type == "INT64"
    assert leaves["timestamp"].physical_type == "FLOAT"


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
    columns = {"/".join(field.path): field for field in metadata.leaf_columns}

    label = columns["label"]
    assert label.physical_type == "BYTE_ARRAY"
    assert label.logical_type == "STRING"
    assert label.converted_type == "UTF8"

    amount = columns["amount"]
    assert amount.physical_type == "FIXED_LEN_BYTE_ARRAY"
    assert amount.logical_type == "DECIMAL(scale=2, precision=9)"
    assert amount.type_length == 4
    assert amount.scale == 2 and amount.precision == 9

    created = columns["created"]
    assert created.logical_type == "TIMESTAMP(unit=MICROS, utc=True)"
    assert created.converted_type == "TIMESTAMP_MICROS"

    day = columns["day"]
    assert day.logical_type == "DATE" and day.converted_type == "DATE"

    # 嵌套 struct 的叶子路径按元组保留，不与含点号的顶层列名混淆。
    assert columns["meta/a"].physical_type == "INT64"
    assert columns["meta/b"].logical_type == "STRING"


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
    text = _write_container(tmp_path / "text.bin", b"", header=b"TEXT")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(text)

    short = tmp_path / "short.bin"
    short.write_bytes(b"PAR1")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(short)

    payload = _file_metadata([_schema_element("x", physical_type=1, num_children=0)])
    bad_tail = _write_container(tmp_path / "tail.bin", payload, trailer_magic=b"XXXX")
    with pytest.raises(NotParquetError):
        read_parquet_metadata(bad_tail)


def test_encrypted_footer_is_rejected(tmp_path: Path) -> None:
    raw = bytearray(_LEROBOT.read_bytes())
    raw[-4:] = b"PARE"
    path = tmp_path / "encrypted.parquet"
    path.write_bytes(bytes(raw))
    with pytest.raises(ParquetEncryptedError):
        read_parquet_metadata(path)


def test_metadata_length_limit_fails_before_reading(tmp_path: Path) -> None:
    path = _write_container(
        tmp_path / "too-big.parquet",
        b"",
        declared_length=MAX_METADATA_BYTES + 1,
    )
    with pytest.raises(ParquetMetadataLimitError):
        read_parquet_metadata(path)


def test_corrupt_metadata_bytes_fail_explicitly(tmp_path: Path) -> None:
    garbage = _write_container(tmp_path / "garbage.parquet", b"\xff\xff\xff\xff")
    with pytest.raises(ParquetMetadataError):
        read_parquet_metadata(garbage)

    wrong_type = _write_container(
        tmp_path / "wrong-type.parquet",
        _struct([(1, _T_BINARY, _binary(b"not-an-int")), (2, _T_LIST, _list_header(_T_STRUCT, 0))]),
    )
    with pytest.raises(ParquetMetadataError, match="version"):
        read_parquet_metadata(wrong_type)


def test_unknown_physical_type_fails(tmp_path: Path) -> None:
    metadata = _file_metadata(
        [
            _schema_element("schema", num_children=1),
            _schema_element("bad", physical_type=99, num_children=0),
        ]
    )
    path = _write_container(tmp_path / "bad-type.parquet", metadata)
    with pytest.raises(ParquetMetadataError, match="schema.type"):
        read_parquet_metadata(path)


def test_schema_children_mismatch_fails(tmp_path: Path) -> None:
    metadata = _file_metadata(
        [
            _schema_element("schema", num_children=2),
            _schema_element("only_one", physical_type=1, num_children=0),
        ]
    )
    path = _write_container(tmp_path / "mismatch.parquet", metadata)
    with pytest.raises(ParquetMetadataError, match="不一致"):
        read_parquet_metadata(path)


def test_missing_required_footer_field_fails(tmp_path: Path) -> None:
    metadata = _file_metadata([_schema_element("schema", num_children=0)], include_num_rows=False)
    path = _write_container(tmp_path / "no-rows.parquet", metadata)
    with pytest.raises(ParquetMetadataError, match="num_rows"):
        read_parquet_metadata(path)


# --- 有界读取：只碰 footer ---------------------------------------------------


def test_only_footer_region_is_read_not_whole_file(tmp_path: Path) -> None:
    raw = _LEROBOT.read_bytes()
    metadata_length = struct.unpack("<I", raw[-8:-4])[0]
    # 在文件头与 footer 之间插入 4 MiB“数据页”，读取量不应随之增长。
    bloated = raw[:4] + b"\x00" * (4 * 1024 * 1024) + raw[4:]
    path = tmp_path / "bloated.parquet"
    path.write_bytes(bloated)

    with path.open("rb", buffering=0) as raw_handle:
        counter = _CountingReader(raw_handle)
        metadata = read_parquet_metadata_from(counter, size=len(bloated))

    assert metadata.num_rows == 3
    assert counter.bytes_read <= metadata_length + 12
    assert counter.bytes_read < len(bloated) // 100


def test_errors_do_not_echo_file_content(tmp_path: Path) -> None:
    payload = b"SECRET-CONTENT-MARKER"
    path = _write_container(tmp_path / "content.parquet", payload)
    with pytest.raises(ParquetMetadataError) as excinfo:
        read_parquet_metadata(path)
    message = str(excinfo.value)
    assert "SECRET-CONTENT-MARKER" not in message
    assert str(tmp_path) not in message
