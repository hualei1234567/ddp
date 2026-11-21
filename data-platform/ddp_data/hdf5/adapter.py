"""HDF5 只读 Adapter：结构预览（DDP-V21-N3-006）。

**输出为什么是“结构预览”而不是 Canonical Episode**

``CanonicalEpisode`` 的必填字段是 ``episode_key`` / ``task_name`` /
``instruction`` / ``media_assets`` 这类**数据集语义**。一个孤立的 HDF5 文件
只有组、数据集与属性名，没有 Episode 标识、指令文本或媒体资产；若硬映射，
只能编造默认值，这违反 ``docs/ddp/07`` 与 Canonical 的“不凭空补齐”原则。
因此本 Adapter 输出 :class:`Hdf5Structure`：真实路径、形状、dtype、压缩、
分块与链接声明，**不产出 Episode**。要得到 Canonical Episode，需要另一层把
数据集级元数据（如 RoboMimic 的 ``data/demo_0.hdf5`` 加配套数据集描述）与
结构组合起来，本任务不伪造这一步。

**为什么不注册进 ``AdapterRegistry``**

注册表的 ``parser`` 契约是 ``bytes -> tuple[CanonicalEpisode, ...]``，且要求在
注册时穷尽声明 Canonical Episode 字段。把本模块注册进去只有两种结果：伪造
Episode 字段，或让 ``detect`` 命中 HDF5 后在导入链路里必然失败。两者都违反
“未知/不支持必须显式失败、不得伪装成功”。因此本 Adapter 由调用方显式选择，
不在内置 Episode 注册表中占位；``adapter_id`` / ``version`` 保持稳定以便留痕。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ddp_data.hdf5.metadata import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    detect_hdf5,
    read_hdf5_structure,
)
from ddp_data.hdf5.models import Hdf5Structure


@dataclass(frozen=True, slots=True)
class Hdf5ReadOnlyAdapter:
    """稳定的只读入口；``adapter_id``/``version`` 用于调用方留痕。

    它刻意只做 ``detect`` / ``inspect``：没有 ``parse`` 到 Canonical Episode
    的能力，也不声称有（原因见模块 docstring）。
    """

    adapter_id: str = ADAPTER_ID
    version: str = ADAPTER_VERSION

    def detect(self, sample: bytes) -> bool:
        return detect_hdf5(sample)

    def inspect(self, path: str | os.PathLike[str]) -> Hdf5Structure:
        return read_hdf5_structure(path)


DEFAULT_HDF5_ADAPTER = Hdf5ReadOnlyAdapter()


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_HDF5_ADAPTER",
    "Hdf5ReadOnlyAdapter",
    "detect_hdf5",
    "read_hdf5_structure",
]
