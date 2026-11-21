"""HDF5 只读结构探测的数据模型（无 IO、无外部依赖）。

所有字段都是**有界结构摘要**：不携带数据集的值、不携带属性的具体值，避免把
大对象或敏感内容带出探测层。属性只保留名字，值一律不进入结果。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Hdf5DatasetSummary:
    """一个数据集的结构摘要；``shape``/``chunks`` 为 ``None`` 表示不可判定。

    HDF5 允许无形状（null dataspace）与未分块的数据集；这些情况给 ``None``
    而不是伪造空元组——``None`` 是“不知道/不适用”，空元组会被读成“零维”。
    """

    path: str
    shape: tuple[int, ...] | None
    dtype: str
    compression: str | None
    chunks: tuple[int, ...] | None
    attributes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hdf5GroupSummary:
    """一个组的路径摘要；成员各自成条，不在此重复。"""

    path: str
    attributes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hdf5LinkSummary:
    """一个非硬链接对象：软链接或外部链接。

    ``target`` 只是链接声明值的**文本**。本模块不解析、不跟随、不校验目标
    是否存在——外部链接的目标常常不在同类存储中，跟随会让探测依赖网络或
    别的文件，也会把“目标缺失”伪造成结构损坏。
    """

    path: str
    link_type: str
    target: str


@dataclass(frozen=True, slots=True)
class Hdf5Structure:
    """一次只读探测的完整产物。

    ``truncated`` 恒为 ``False``：超限一律抛 :class:`Hdf5MetadataLimitError`，
    **不做静默截断**——一个被悄悄截断的结构列表会被下游当成“文件里就这么多
    对象”。保留该字段是为了让调用方显式断言“我拿到的是完整结构”。
    """

    adapter_id: str
    adapter_version: str
    dataset_count: int
    group_count: int
    attribute_count: int
    truncated: bool
    datasets: tuple[Hdf5DatasetSummary, ...]
    groups: tuple[Hdf5GroupSummary, ...]
    links: tuple[Hdf5LinkSummary, ...]

    def as_metadata(self) -> dict[str, object]:
        """有界、可 JSON 序列化的摘要；不含路径与数据值。

        调用方可把它嵌进 ``RawAsset.raw_metadata``（如 ``{"hdf5": ...}``），
        与既有导入来源留痕并存，无需新增数据库列。
        """
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "dataset_count": self.dataset_count,
            "group_count": self.group_count,
            "attribute_count": self.attribute_count,
            "truncated": self.truncated,
            "datasets": [
                {
                    "path": item.path,
                    "shape": list(item.shape) if item.shape is not None else None,
                    "dtype": item.dtype,
                    "compression": item.compression,
                    "chunks": list(item.chunks) if item.chunks is not None else None,
                    "attributes": list(item.attributes),
                }
                for item in self.datasets
            ],
            "groups": [
                {"path": item.path, "attributes": list(item.attributes)}
                for item in self.groups
            ],
            "links": [
                {"path": item.path, "link_type": item.link_type, "target": item.target}
                for item in self.links
            ],
        }


__all__ = [
    "Hdf5DatasetSummary",
    "Hdf5GroupSummary",
    "Hdf5LinkSummary",
    "Hdf5Structure",
]
