"""Parquet 只读 Adapter：表结构预览与显式字段缺失声明（DDP-V21-N3-003）。

**输出为什么是“表预览”而不是 Canonical Episode**

``CanonicalEpisode`` 的必填字段是 ``episode_key`` / ``task_name`` /
``instruction`` / ``media_assets`` 等**数据集语义**。一个孤立的 Parquet 表只有
列与行，没有 Episode 标识、指令文本或媒体资产；若硬映射，只能编造默认值，
这违反 ``docs/ddp/07`` 与 Canonical 的“不凭空补齐”原则。因此本 Adapter 输出
:class:`ParquetTablePreview`：真实 schema、行数、行组数、可识别的关键列，以及
**显式缺失的期望列**。要得到 Canonical Episode，需要另一层把数据集级元数据
（如 ``meta/info.json``、episodes 元数据）与表数据组合起来，本任务不伪造这一步。

**为什么不注册进 ``AdapterRegistry``**

注册表的 ``parser`` 契约是 ``bytes -> tuple[CanonicalEpisode, ...]``，且要求在
注册时穷尽声明 Canonical Episode 字段。把本模块注册进去只有两种结果：伪造
Episode 字段，或让 ``detect`` 命中 Parquet 后在导入链路里必然失败。两者都违反
“未知/不支持必须显式失败、不得伪装成功”。因此本 Adapter 由表级调用方显式选择，
不在内置 Episode 注册表中占位；``adapter_id`` / ``version`` 保持稳定以便留痕。

**关键列与 ``missing_fields`` 的口径**

- ``missing_fields`` 是**表级期望列**的缺失，不是 Canonical Episode 字段缺失；
- 只有识别为 ``lerobot_tabular`` 时才套用 LeRobot 的期望列清单。通用表没有
  可声明的期望 schema，``missing_fields`` 为空表示“未声明期望”，不表示“字段齐全”；
- 期望列全部通过 ``key_columns`` 给出“期望名 → 实际顶层列名/None”，
  缺失即 ``None``，绝不拿空值冒充存在。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ddp_data.parquet.metadata import read_parquet_metadata
from ddp_data.parquet.models import ParquetMetadata, ParquetSchemaField

ADAPTER_ID = "parquet.table_readonly"
ADAPTER_VERSION = "1.0.0"

_HEADER_BYTES = 4
_MIN_FILE_BYTES = 12
_MAGIC = b"PAR1"
_ENCRYPTED_MAGIC = b"PARE"
_LEROBOT_LAYOUT_MARKERS = frozenset({"episode_index", "frame_index", "timestamp"})


@dataclass(frozen=True, slots=True)
class ExpectedColumn:
    """一种表布局期望存在的列。``required`` 只影响 ``missing_required_fields``。"""

    name: str
    purpose: str
    required: bool = False


LEROBOT_TABULAR_COLUMNS: tuple[ExpectedColumn, ...] = (
    ExpectedColumn("episode_index", "Episode 序号", required=True),
    ExpectedColumn("frame_index", "Episode 内帧序号", required=True),
    ExpectedColumn("timestamp", "帧时间戳（秒）", required=True),
    ExpectedColumn("observation.state", "观测/状态向量", required=True),
    ExpectedColumn("action", "动作向量", required=True),
    ExpectedColumn("task_index", "任务序号"),
    ExpectedColumn("index", "数据集全局行序号"),
    ExpectedColumn("next.done", "回合结束标记"),
    ExpectedColumn("next.reward", "即时奖励"),
)


class ParquetLayout(StrEnum):
    """已识别的表布局；只是提示，不是权威结论。"""

    LEROBOT_TABULAR = "lerobot_tabular"
    GENERIC_TABLE = "generic_table"


@dataclass(frozen=True, slots=True)
class ParquetColumnSummary:
    """一个叶子物理列的可解释摘要；组字段不在此列。"""

    path: tuple[str, ...]
    physical_type: str
    logical_type: str | None
    converted_type: str | None
    repetition: str

    @property
    def path_text(self) -> str:
        return "/".join(self.path)


@dataclass(frozen=True, slots=True)
class ParquetTablePreview:
    """一次只读预览的完整产物。所有“没有”都以显式字段表达。"""

    adapter_id: str
    adapter_version: str
    layout: ParquetLayout
    num_rows: int
    row_group_count: int
    created_by: str | None
    top_level_fields: tuple[str, ...]
    columns: tuple[ParquetColumnSummary, ...]
    key_columns: Mapping[str, str | None]
    missing_fields: frozenset[str]
    missing_required_fields: frozenset[str]
    unmapped_columns: frozenset[str]


def detect_parquet(sample: bytes) -> bool:
    """按**完整文件字节**判断是否为 Parquet；截断片段不会命中。

    Parquet 的格式证据是首尾 ``PAR1``（加密 footer 为 ``PARE``）。只有文件头
    没有文件尾不能证明是 Parquet，因此不做“前缀命中”的宽松判断。
    """
    if len(sample) < _MIN_FILE_BYTES:
        return False
    if sample[:_HEADER_BYTES] != _MAGIC:
        return False
    return sample[-_HEADER_BYTES:] in (_MAGIC, _ENCRYPTED_MAGIC)


def summarize_parquet(
    path: str | os.PathLike[str],
    *,
    expected_columns: Sequence[ExpectedColumn] | None = None,
) -> ParquetTablePreview:
    """读取 Parquet 并按表布局产出预览。

    ``expected_columns`` 为 ``None`` 时按识别出的布局使用默认期望列；
    显式传入（包括空序列）时以传入值为准，便于调用方声明自己的列契约。
    """
    return summarize_metadata(read_parquet_metadata(path), expected_columns=expected_columns)


def summarize_metadata(
    metadata: ParquetMetadata,
    *,
    expected_columns: Sequence[ExpectedColumn] | None = None,
) -> ParquetTablePreview:
    """把已读取的元数据收敛为预览；不访问文件，便于测试与复用。"""
    top_level = tuple(field.name for field in metadata.top_level_fields)
    layout = _detect_layout(top_level)
    expected = _resolve_expected(layout, expected_columns)
    names = [item.name for item in expected]
    if len(set(names)) != len(names):
        raise ValueError("expected_columns 中的列名不能重复")
    present = set(top_level)
    key_columns = {item.name: item.name if item.name in present else None for item in expected}
    missing = frozenset(name for name, actual in key_columns.items() if actual is None)
    missing_required = frozenset(
        item.name for item in expected if item.required and item.name not in present
    )
    expected_names = {item.name for item in expected}
    return ParquetTablePreview(
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        layout=layout,
        num_rows=metadata.num_rows,
        row_group_count=metadata.row_group_count,
        created_by=metadata.created_by,
        top_level_fields=top_level,
        columns=tuple(_column_summary(field) for field in metadata.leaf_columns),
        key_columns=MappingProxyType(key_columns),
        missing_fields=missing,
        missing_required_fields=missing_required,
        unmapped_columns=frozenset(present - expected_names),
    )


def _detect_layout(top_level: Sequence[str]) -> ParquetLayout:
    if _LEROBOT_LAYOUT_MARKERS <= set(top_level):
        return ParquetLayout.LEROBOT_TABULAR
    return ParquetLayout.GENERIC_TABLE


def _resolve_expected(
    layout: ParquetLayout,
    override: Sequence[ExpectedColumn] | None,
) -> tuple[ExpectedColumn, ...]:
    if override is not None:
        return tuple(override)
    if layout is ParquetLayout.LEROBOT_TABULAR:
        return LEROBOT_TABULAR_COLUMNS
    return ()


def _column_summary(field: ParquetSchemaField) -> ParquetColumnSummary:
    if field.physical_type is None:  # pragma: no cover - 由 schema 构建阶段保证
        raise ValueError("叶子列缺少物理类型")
    return ParquetColumnSummary(
        path=field.path,
        physical_type=field.physical_type,
        logical_type=field.logical_type,
        converted_type=field.converted_type,
        repetition=field.repetition,
    )


@dataclass(frozen=True, slots=True)
class ParquetReadOnlyAdapter:
    """稳定的只读入口；``adapter_id``/``version`` 用于调用方留痕。

    它刻意只做 ``detect`` / ``read_metadata`` / ``inspect``：没有 ``parse``
    到 Canonical Episode 的能力，也不声称有（原因见模块 docstring）。
    """

    adapter_id: str = ADAPTER_ID
    version: str = ADAPTER_VERSION

    def detect(self, sample: bytes) -> bool:
        return detect_parquet(sample)

    def read_metadata(self, path: str | os.PathLike[str]) -> ParquetMetadata:
        return read_parquet_metadata(path)

    def inspect(
        self,
        path: str | os.PathLike[str],
        *,
        expected_columns: Sequence[ExpectedColumn] | None = None,
    ) -> ParquetTablePreview:
        return summarize_parquet(path, expected_columns=expected_columns)


DEFAULT_PARQUET_ADAPTER = ParquetReadOnlyAdapter()


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_PARQUET_ADAPTER",
    "ExpectedColumn",
    "LEROBOT_TABULAR_COLUMNS",
    "ParquetColumnSummary",
    "ParquetLayout",
    "ParquetReadOnlyAdapter",
    "ParquetTablePreview",
    "detect_parquet",
    "summarize_metadata",
    "summarize_parquet",
]
