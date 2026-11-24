"""HDF5 payload 读取（V51-001/002/003，Adapter 能力 PAYLOAD_PARSE）。

**与 ``metadata.py`` 的分工**

``metadata.py`` 只读**结构**（路径/形状/dtype），不碰数据值——那是探测层。
本模块读**真实数值**，是 V5.1「从 'HDF5 支持' 走到 'HDF5 payload 可读'」的
关键一步。两者共用同一套有界与脱敏纪律。

**为什么要"有界"**

一份真实机器人 HDF5 可能有数百 MB 到数 GB 的数值数据。无界读取会：
- 把内存打爆（边缘工作站只有几 GB）；
- 让"读一个 Episode"变成"读整个数据集"，失去增量处理能力。

因此本模块只提供**切片读取**：调用方给 ``start``/``length``，返回该窗口。
全量读取由调用方分块循环，不在库内一次完成。

**不做什么**

- 不做格式猜测：路径必须由调用方给出（来自 ``metadata.py`` 的结构探测）
- 不做单位/语义推断：``dtype``/``shape`` 如实返回，语义归上层 Canonical 映射
- 不做静默降采样：超限抛错，不偷偷截断
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddp_data.hdf5.errors import (
    Hdf5InputError,
    Hdf5MetadataError,
    Hdf5MetadataLimitError,
    Hdf5UnavailableError,
)
from ddp_data.hdf5.metadata import (
    HDF5_SIGNATURE,
    MAX_SIGNATURE_OFFSET,
    _display_name,
    _ensure_sdk,
)

#: 单次切片读取的最大元素数（防止一次读爆）。
#: 32 Mi 个元素 × 8 字节 ≈ 256 MiB，对边缘工作站已是上限。
MAX_SLICE_ELEMENTS = 32 * 1024 * 1024

#: 单次切片读取的最大字节数（与元素数取更严格者）。
MAX_SLICE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Hdf5DatasetPayload:
    """一次切片读取的结果：真实数值 + 定位信息。

    ``values`` 是**扁平化**的 Python 标量元组。为什么不用 numpy 数组：
    1. 本模块属于 ``data-platform`` 纯数据层，返回 numpy 会把 numpy 变成
       下游（backend）的隐式依赖；
    2. Canonical 层关心的是「可序列化的真实值」，不需要数组运算；
    3. 扁平化强制调用方显式处理 ``shape``（避免误把 2D 当 1D）。
    """

    path: str
    dtype: str
    shape: tuple[int, ...]
    #: 本次切片的起始行（仅第一维有意义）
    start: int
    #: 本次切片的行数
    length: int
    #: 扁平化后的值；长度应等于 ``length × prod(shape[1:])``
    values: tuple[float | int | bool, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "start": self.start,
            "length": self.length,
            "values": list(self.values),
        }


@dataclass(frozen=True, slots=True)
class Hdf5AttributeValue:
    """一个属性的真实值（有界）。

    属性常用于承载标定参数、版本号、单位等元信息，是真实 payload 的一部分。
    只支持标量与短序列；大数组属性不读（那是数据不是属性）。
    """

    path: str
    name: str
    dtype: str
    value: object


def detect_hdf5_payload(sample: bytes) -> bool:
    """payload 层复用结构层的签名检测（同一格式判定，不重复实现）。"""
    if not sample:
        return False
    limit = min(len(sample), MAX_SIGNATURE_OFFSET)
    for offset in range(0, limit - len(HDF5_SIGNATURE) + 1, 512):
        if sample[offset : offset + len(HDF5_SIGNATURE)] == HDF5_SIGNATURE:
            return True
    return False


def read_hdf5_dataset_slice(
    path: str | os.PathLike[str],
    dataset_path: str,
    *,
    start: int = 0,
    length: int | None = None,
    max_elements: int = MAX_SLICE_ELEMENTS,
    max_bytes: int = MAX_SLICE_BYTES,
) -> Hdf5DatasetPayload:
    """读取一个数据集的第一维切片（真实数值）。

    ``length=None`` 表示读到第一维末尾（仍受 ``max_elements`` 约束）。

    **只支持按第一维切片**：多维数据集的「第 i 行」是天然的处理单位
    （一帧、一个采样点、一个关节向量）。跨维复杂索引不做——那会让
    切片语义变得不可预测，且真实场景几乎不需要。
    """
    _ensure_sdk()
    if max_elements <= 0 or max_bytes <= 0:
        raise Hdf5MetadataLimitError("max_elements 与 max_bytes 必须为正")
    if start < 0:
        raise Hdf5MetadataError("start 不得为负")
    if length is not None and length <= 0:
        raise Hdf5MetadataError("length 必须为正（读 0 行无意义，用不调用代替）")
    _require_dataset_path(dataset_path)

    resolved = _resolve_file(path)
    try:
        import h5py
    except Exception:  # noqa: BLE001 - SDK 缺失已由 _ensure_sdk 拦过，此处理论不可达
        raise Hdf5UnavailableError("缺少官方 h5py SDK") from None

    try:
        with h5py.File(resolved, "r") as handle:
            if dataset_path not in handle:
                raise Hdf5MetadataError(f"数据集不存在：{dataset_path}")
            dataset = handle[dataset_path]
            if not isinstance(dataset, h5py.Dataset):
                raise Hdf5MetadataError(f"路径不是数据集：{dataset_path}")
            return _read_slice(
                dataset,
                dataset_path,
                start=start,
                length=length,
                max_elements=max_elements,
                max_bytes=max_bytes,
            )
    except (Hdf5MetadataError, Hdf5MetadataLimitError):
        raise
    except OSError:
        # 不链式保留原始异常：底层错误可能携带绝对路径。
        raise Hdf5InputError(f"HDF5 文件读取失败：{_display_name(resolved)}") from None


def _read_slice(
    dataset: Any,
    dataset_path: str,
    *,
    start: int,
    length: int | None,
    max_elements: int,
    max_bytes: int,
) -> Hdf5DatasetPayload:
    shape = tuple(int(dim) for dim in dataset.shape)
    if not shape:
        # 标量数据集（null dataspace / 0 维）：不是"按帧"的数据，不支持切片。
        raise Hdf5MetadataError(f"数据集无维度，不支持切片：{dataset_path}")
    total = shape[0]
    if start >= total:
        raise Hdf5MetadataError(
            f"start={start} 超出数据集第一维长度 {total}：{dataset_path}"
        )
    span = total - start if length is None else min(length, total - start)
    if span <= 0:
        raise Hdf5MetadataError("切片跨度必须为正")

    # 行内元素数（shape[1:] 的乘积）；标量行按 1 计。
    row_elements = 1
    for dim in shape[1:]:
        row_elements *= dim
    element_count = span * row_elements
    if element_count > max_elements:
        raise Hdf5MetadataLimitError(
            f"切片元素数 {element_count} 超过上限 {max_elements}：{dataset_path}；"
            "请缩小 length 后分块读取"
        )

    dtype = str(dataset.dtype)
    itemsize = int(dataset.dtype.itemsize) if dataset.dtype.itemsize else 1
    if element_count * itemsize > max_bytes:
        raise Hdf5MetadataLimitError(
            f"切片字节数约 {element_count * itemsize} 超过上限 {max_bytes}：{dataset_path}"
        )

    try:
        block = dataset[start : start + span]
    except Exception as exc:  # noqa: BLE001 - h5py 错误类型多样，统一翻译
        raise Hdf5MetadataError(f"数据集读取失败：{dataset_path}") from exc

    return Hdf5DatasetPayload(
        path=dataset_path,
        dtype=dtype,
        shape=shape,
        start=start,
        length=span,
        values=_flatten(block),
    )


def _flatten(block: Any) -> tuple[float | int | bool, ...]:
    """把 numpy 多维块扁平成 Python 标量元组（避免 numpy 泄漏到下游）。

    ``bool_`` 必须先于 ``int`` 判断：bool 在 numpy 里是 int 的子类，
    顺序反了会把布尔通道读成 0/1，语义就变了。
    """
    result: list[float | int | bool] = []
    try:
        flat = block.reshape(-1)
    except AttributeError:
        # 0 维或非数组：直接取标量
        return (_py_scalar(block),)
    for item in flat:
        result.append(_py_scalar(item))
    return tuple(result)


def _py_scalar(value: Any) -> float | int | bool:
    import numpy as np

    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def read_hdf5_attributes(
    path: str | os.PathLike[str],
    object_path: str = "/",
    *,
    max_text_length: int = 4096,
) -> tuple[Hdf5AttributeValue, ...]:
    """读取一个组/数据集的属性真实值（有界）。

    属性里常见标定参数、单位、版本号——这些是「真实 payload 的元信息」，
    与结构层的"只保留属性名"互补。
    """
    _ensure_sdk()
    if max_text_length <= 0:
        raise Hdf5MetadataLimitError("max_text_length 必须为正")
    resolved = _resolve_file(path)
    try:
        import h5py
    except Exception:  # noqa: BLE001
        raise Hdf5UnavailableError("缺少官方 h5py SDK") from None

    try:
        with h5py.File(resolved, "r") as handle:
            target_path = object_path or "/"
            if target_path != "/" and target_path not in handle:
                raise Hdf5MetadataError(f"对象不存在：{target_path}")
            target = handle[target_path]
            values: list[Hdf5AttributeValue] = []
            for name in target.attrs:
                raw = target.attrs[name]
                value = _bounded_attribute(raw, max_text_length=max_text_length)
                if value is None:
                    continue
                values.append(
                    Hdf5AttributeValue(
                        path=target_path,
                        name=str(name),
                        dtype=str(getattr(raw, "dtype", type(raw).__name__)),
                        value=value,
                    )
                )
            return tuple(values)
    except (Hdf5MetadataError, Hdf5MetadataLimitError):
        raise
    except OSError:
        raise Hdf5InputError(f"HDF5 文件读取失败：{_display_name(resolved)}") from None


def _bounded_attribute(raw: Any, *, max_text_length: int) -> object | None:
    """属性的有界表示；``None`` 表示「不读」（大数组属性）。"""
    import numpy as np

    if isinstance(raw, np.ndarray):
        # 大数组属性不读：属性应是元信息，不是数据本身。
        if raw.size > 64:
            return None
        return [_py_scalar(item) for item in raw.reshape(-1)]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
        return text[:max_text_length]
    if isinstance(raw, str):
        return raw[:max_text_length]
    if isinstance(raw, np.bool_):
        return bool(raw)
    if isinstance(raw, np.integer):
        return int(raw)
    if isinstance(raw, np.floating):
        return float(raw)
    return (
        raw if isinstance(raw, bool | int | float | str) else str(raw)[:max_text_length]
    )


def _require_dataset_path(dataset_path: str) -> None:
    if not isinstance(dataset_path, str) or not dataset_path:
        raise Hdf5MetadataError("dataset_path 必须是非空字符串")
    if not dataset_path.startswith("/"):
        raise Hdf5MetadataError("dataset_path 必须以 / 开头（HDF5 绝对路径）")


def _resolve_file(path: str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(path)
    except TypeError:
        raise Hdf5InputError("HDF5 路径必须是字符串或 Path") from None
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise Hdf5InputError(f"HDF5 文件不存在：{_display_name(candidate)}") from None
    except OSError:
        raise Hdf5InputError(
            f"无法访问 HDF5 文件：{_display_name(candidate)}"
        ) from None
    if not resolved.is_file():
        raise Hdf5InputError(f"HDF5 路径不是普通文件：{_display_name(candidate)}")
    return resolved


__all__ = [
    "MAX_SLICE_BYTES",
    "MAX_SLICE_ELEMENTS",
    "Hdf5AttributeValue",
    "Hdf5DatasetPayload",
    "detect_hdf5_payload",
    "read_hdf5_attributes",
    "read_hdf5_dataset_slice",
]
