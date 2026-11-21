"""HDF5 文件结构只读探测（DDP-V21-N3-006）。

**实现来源**

使用官方 **h5py**（``h5py.File`` 只读模式），不自研 HDF5 超块/B-tree 解析。
本模块只遍历**链路结构**（组、数据集、属性名、链接声明），不解码数据集的值：

- **有界**：用计数包装器限制实际读取字节数；数据集/组/属性数量与属性文本
  长度同样有上限。超限一律抛 :class:`Hdf5MetadataLimitError`，**不静默截断**。
- **显式**：非 HDF5、结构损坏、SDK 缺失、超限全部抛 :mod:`errors` 中的异常，
  绝不返回空列表冒充“读到了但没有对象”。
- **不跟随链接**：软链接与外部链接只登记声明文本，不解析目标。外部链接的
  目标可能不在同类存储中，跟随会让探测依赖网络或别的文件，并把“目标缺失”
  伪造成结构损坏。
- **脱敏**：对外错误消息不含绝对路径；底层 h5py/HDF5 错误（可能携带路径与
  文件内容）不作为错误文本透出。

只读范围：组/数据集的路径与结构属性（形状、dtype、压缩、分块）、属性**名字**、
链接声明。数据集的值、属性值、压缩后的块内容均不在返回结果内。

**为什么读文件时会读到接近全文件大小**

HDF5 的元数据不是尾部集中索引，超块、对象头与 B-tree 分散在文件中。``h5py``
打开与遍历结构时会按需读取这些元数据块，官方 SDK 不对上层暴露“只读元数据”
的裁剪接口。因此本模块用**读取字节上限**兜底：超过 ``max_metadata_bytes``
即显式失败，避免一次探测把超大文件整体拖进内存或读放大。

**依赖**：``data-platform/requirements.txt`` 固定 ``h5py==3.16.0``；
后端测试 job 与本地 venv 必须安装该文件，不能依赖开发机巧合。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from ddp_data.hdf5.errors import (
    Hdf5InputError,
    Hdf5MetadataError,
    Hdf5MetadataLimitError,
    Hdf5UnavailableError,
    NotHdf5Error,
)
from ddp_data.hdf5.models import (
    Hdf5DatasetSummary,
    Hdf5GroupSummary,
    Hdf5LinkSummary,
    Hdf5Structure,
)

try:
    import h5py as _h5py
except ImportError:  # pragma: no cover - 依赖缺失时保留显式错误路径
    _h5py = None

ADAPTER_ID = "hdf5.structure_readonly"
ADAPTER_VERSION = "1.0.0"

HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
# HDF5 允许 user block，签名落在 512 的整数倍偏移上；1 MiB 覆盖常见取值。
MAX_SIGNATURE_OFFSET = 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024 * 1024
# 真实机器人 HDF5 数据集通常把多个 demo 放在一个文件里：RoboMimic v1.5
# ``lift/ph`` 的 59 条轨迹即有 6008 数据集 / 1802 组。上限按“单文件能容纳一份
# 中等规模演示集”取值，同时仍由 ``max_metadata_bytes`` 兜底防止读放大。
MAX_DATASETS = 50_000
MAX_GROUPS = 20_000
MAX_ATTRIBUTES = 256
MAX_TEXT_LENGTH = 4096
MAX_LINK_TARGET_LENGTH = 4096
_DISPLAY_NAME_LIMIT = 128


class _BoundedReader:
    """统计并限制累计读取字节数；超限抛显式异常而不是截断。

    作为 ``h5py.File`` 的 file-like 驱动使用；HDF5 C 库以 ``read`` 拉取数据，
    因此只需实现 ``read``/``seek``/``tell``。
    """

    def __init__(self, handle: BinaryIO, limit: int) -> None:
        self._handle = handle
        self._limit = limit
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """按调用方请求量读取，但先用剩余预算封顶。

        不能把每次读取截到固定块大小：file-like 驱动的短读会被 HDF5 C 库当成
        “文件提前结束”，从而把有效文件误判为损坏。这里改为按预算封顶——
        预算耗尽时返回空字节（等价 EOF）并**显式**抛错，绝不静默截断。
        """
        remaining = self._limit - self.bytes_read
        if remaining <= 0:
            raise Hdf5MetadataLimitError(f"HDF5 结构读取超过 {self._limit} 字节上限")
        capped_by_budget = size is None or size < 0 or size >= remaining
        wanted = remaining if capped_by_budget else size
        data = self._handle.read(wanted)
        self.bytes_read += len(data)
        if capped_by_budget and self.bytes_read >= self._limit:
            # 预算正好被当作读取量：继续读就会越过上限，且返回短读会被 HDF5
            # C 库当成 EOF 从而产出“表面完整”的截断结构。这里显式失败。
            raise Hdf5MetadataLimitError(f"HDF5 结构读取达到 {self._limit} 字节上限")
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        return self._handle.tell()


def detect_hdf5(sample: bytes) -> bool:
    """按**完整文件字节**判断是否为 HDF5；截断片段不会命中。

    签名 ``\\x89HDF\\r\\n\\x1a\\n`` 允许出现在 user block 之后的 512 整数倍
    偏移上（HDF5 规范），因此按该步长扫描到 ``MAX_SIGNATURE_OFFSET``；
    不做“任意偏移出现即命中”的宽松判断。
    """
    if not sample:
        return False
    limit = min(len(sample), MAX_SIGNATURE_OFFSET)
    for offset in range(0, limit - len(HDF5_SIGNATURE) + 1, 512):
        if sample[offset : offset + len(HDF5_SIGNATURE)] == HDF5_SIGNATURE:
            return True
    return False


def read_hdf5_structure(
    path: str | os.PathLike[str],
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_datasets: int = MAX_DATASETS,
    max_groups: int = MAX_GROUPS,
    max_attributes: int = MAX_ATTRIBUTES,
    max_text_length: int = MAX_TEXT_LENGTH,
) -> Hdf5Structure:
    """读取 HDF5 的结构摘要；不读取数据集的值。"""
    _ensure_sdk()
    _validate_limits(
        max_metadata_bytes, max_datasets, max_groups, max_attributes, max_text_length
    )
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
    try:
        with resolved.open("rb", buffering=0) as handle:
            return read_hdf5_structure_from(
                handle,
                max_metadata_bytes=max_metadata_bytes,
                max_datasets=max_datasets,
                max_groups=max_groups,
                max_attributes=max_attributes,
                max_text_length=max_text_length,
            )
    except Hdf5MetadataLimitError:
        raise
    except OSError:
        # 不链式保留原始异常：底层错误可能携带绝对路径。
        raise Hdf5InputError(f"HDF5 文件读取失败：{_display_name(candidate)}") from None


def read_hdf5_structure_from(
    handle: BinaryIO,
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    max_datasets: int = MAX_DATASETS,
    max_groups: int = MAX_GROUPS,
    max_attributes: int = MAX_ATTRIBUTES,
    max_text_length: int = MAX_TEXT_LENGTH,
) -> Hdf5Structure:
    """从可 seek 的二进制流读取结构摘要。

    暴露这一层是为了让调用方使用**对象存储下载到临时文件的句柄**或测试替身，
    也便于验证“不会无界读取”。
    """
    _ensure_sdk()
    _validate_limits(
        max_metadata_bytes, max_datasets, max_groups, max_attributes, max_text_length
    )
    reader = _BoundedReader(handle, max_metadata_bytes)
    assert _h5py is not None  # _ensure_sdk 已校验
    try:
        h5_handle = _h5py.File(reader, "r")
    except Hdf5MetadataLimitError:
        raise
    except (OSError, ValueError):
        # h5py 对非 HDF5 / 损坏文件抛 OSError；不链式保留（可能含路径与内容）。
        raise NotHdf5Error("文件不是有效的 HDF5，或结构已损坏") from None
    try:
        return _collect_structure(
            h5_handle,
            max_datasets=max_datasets,
            max_groups=max_groups,
            max_attributes=max_attributes,
            max_text_length=max_text_length,
        )
    except Hdf5MetadataLimitError:
        raise
    except (OSError, ValueError):
        raise Hdf5MetadataError("HDF5 结构元数据无法解码") from None
    finally:
        try:
            h5_handle.close()
        except OSError:
            # 关闭失败不影响已取得的只读结果；不得吞掉上面抛出的真实异常。
            pass


def _collect_structure(
    handle: object,
    *,
    max_datasets: int,
    max_groups: int,
    max_attributes: int,
    max_text_length: int,
) -> Hdf5Structure:
    h5py_module = _h5py
    assert h5py_module is not None
    datasets: list[Hdf5DatasetSummary] = []
    groups: list[Hdf5GroupSummary] = []
    links: list[Hdf5LinkSummary] = []
    attribute_count = len(
        _attribute_names(
            handle, max_attributes=max_attributes, max_text_length=max_text_length
        )
    )

    def visit(name: str, obj: object) -> None:
        nonlocal attribute_count
        path = "/" + name
        names = _attribute_names(
            obj, max_attributes=max_attributes, max_text_length=max_text_length
        )
        attribute_count += len(names)
        if isinstance(obj, h5py_module.Dataset):
            if len(datasets) >= max_datasets:
                raise Hdf5MetadataLimitError(f"HDF5 数据集数量超过 {max_datasets} 上限")
            datasets.append(_dataset_summary(path, obj, names, max_text_length))
        elif isinstance(obj, h5py_module.Group):
            if len(groups) >= max_groups:
                raise Hdf5MetadataLimitError(f"HDF5 组数量超过 {max_groups} 上限")
            groups.append(Hdf5GroupSummary(path=path, attributes=names))

    # 根组自身不是 visititems 的访问对象，因此单独计数它的属性。
    handle.visititems(visit)
    _collect_links(
        handle,
        links=links,
        max_text_length=max_text_length,
        max_groups=max_groups,
    )
    return Hdf5Structure(
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        dataset_count=len(datasets),
        group_count=len(groups),
        attribute_count=attribute_count,
        truncated=False,
        datasets=tuple(datasets),
        groups=tuple(groups),
        links=tuple(links),
    )


def _collect_links(
    root: object,
    *,
    links: list[Hdf5LinkSummary],
    max_text_length: int,
    max_groups: int,
) -> None:
    """登记软链接/外部链接；不跟随，也不进入链接目标。

    用显式栈而不是递归：HDF5 允许任意深的组嵌套，递归遍历在深层结构上会抛
    ``RecursionError`` 绕过全部上限。这里同时用 ``max_groups`` 约束栈的总
    展开次数，保证遍历本身有界。
    """
    h5py_module = _h5py
    assert h5py_module is not None
    stack: list[tuple[object, str]] = [(root, "")]
    visited = 0
    while stack:
        group, prefix = stack.pop()
        visited += 1
        if visited > max_groups + 1:
            raise Hdf5MetadataLimitError(f"HDF5 链接遍历超过 {max_groups} 个组的预算")
        for name in group:  # type: ignore[attr-defined]
            path = f"{prefix}/{name}" if prefix else f"/{name}"
            link = group.get(name, getlink=True)  # type: ignore[attr-defined]
            if isinstance(link, h5py_module.SoftLink):
                _append_link(links, path, "soft", link.path, max_text_length)
            elif isinstance(link, h5py_module.ExternalLink):
                target = f"{link.filename}:{link.path}"
                _append_link(links, path, "external", target, max_text_length)
            else:
                try:
                    member = group[name]  # type: ignore[index]
                except (KeyError, OSError):
                    continue
                if isinstance(member, h5py_module.Group):
                    stack.append((member, path))


def _append_link(
    links: list[Hdf5LinkSummary],
    path: str,
    link_type: str,
    target: str,
    max_text_length: int,
) -> None:
    if len(path) > max_text_length or len(target) > max_text_length:
        raise Hdf5MetadataLimitError(f"HDF5 链接文本超过 {max_text_length} 字符上限")
    links.append(
        Hdf5LinkSummary(
            path=_clean_text(path, max_text_length),
            link_type=link_type,
            target=_clean_text(target, max_text_length),
        )
    )


def _dataset_summary(
    path: str,
    dataset: object,
    names: tuple[str, ...],
    max_text_length: int,
) -> Hdf5DatasetSummary:
    shape = getattr(dataset, "shape", None)
    chunks = getattr(dataset, "chunks", None)
    compression = getattr(dataset, "compression", None)
    return Hdf5DatasetSummary(
        path=_clean_text(path, max_text_length),
        shape=None if shape is None else tuple(int(dim) for dim in shape),
        dtype=str(getattr(dataset, "dtype", "")),
        compression=None if compression is None else str(compression),
        chunks=None if chunks is None else tuple(int(dim) for dim in chunks),
        attributes=names,
    )


def _attribute_names(
    obj: object,
    *,
    max_attributes: int,
    max_text_length: int,
) -> tuple[str, ...]:
    """只读取属性**名字**；属性值一律不进入结果。

    属性个数用 ``len`` 判断，避免先枚举再发现超限；名字长度也受上限约束。
    """
    attrs = getattr(obj, "attrs", None)
    if attrs is None:
        return ()
    try:
        total = len(attrs)
    except TypeError:
        return ()
    if total > max_attributes:
        raise Hdf5MetadataLimitError(f"HDF5 属性数量超过 {max_attributes} 上限")
    names: list[str] = []
    for name in attrs:
        text = str(name)
        if len(text) > max_text_length:
            raise Hdf5MetadataLimitError(f"HDF5 属性名超过 {max_text_length} 字符上限")
        names.append(text)
    return tuple(names)


def _clean_text(text: str, limit: int) -> str:
    """去掉不可打印字符并截断；避免日志注入与超长名称。"""
    cleaned = "".join(char if char.isprintable() else "?" for char in text)
    return cleaned[:limit]


def _display_name(path: Path) -> str:
    """错误信息里只留可打印文件名；控制字符替换掉，避免日志注入。"""
    name = path.name or "?"
    cleaned = "".join(char if char.isprintable() else "?" for char in name)
    return cleaned[:_DISPLAY_NAME_LIMIT]


def _ensure_sdk() -> None:
    if _h5py is None:
        raise Hdf5UnavailableError("运行环境缺少 h5py，无法解析 HDF5 结构")


def _validate_limits(
    max_metadata_bytes: int,
    max_datasets: int,
    max_groups: int,
    max_attributes: int,
    max_text_length: int,
) -> None:
    for label, value in (
        ("max_metadata_bytes", max_metadata_bytes),
        ("max_datasets", max_datasets),
        ("max_groups", max_groups),
        ("max_attributes", max_attributes),
        ("max_text_length", max_text_length),
    ):
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} 必须是正整数")


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "HDF5_SIGNATURE",
    "MAX_ATTRIBUTES",
    "MAX_DATASETS",
    "MAX_GROUPS",
    "MAX_METADATA_BYTES",
    "MAX_TEXT_LENGTH",
    "detect_hdf5",
    "read_hdf5_structure",
    "read_hdf5_structure_from",
]
