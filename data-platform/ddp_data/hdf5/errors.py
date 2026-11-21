"""HDF5 只读结构探测的异常族（DDP-V21-N3-006）。

分四类：输入（缺文件/不是普通文件）、格式（不是 HDF5、结构损坏）、环境
（官方 SDK 不可用）、元数据（解码失败、超限、链接类型不支持）。全部继承
:class:`Hdf5ProbeError`，调用方可以统一捕获，但**不得**把它当成“没有数据”
静默吞掉。
"""

from __future__ import annotations


class Hdf5ProbeError(Exception):
    """HDF5 只读探测失败基类；调用方必须显式处理。"""

    code = "hdf5_probe_failed"


class Hdf5InputError(Hdf5ProbeError):
    """输入不是可读的普通文件。"""

    code = "invalid_input"


class NotHdf5Error(Hdf5ProbeError):
    """内容缺少 HDF5 签名，或结构在读取时被判为损坏。"""

    code = "not_hdf5"


class Hdf5UnavailableError(Hdf5ProbeError):
    """运行环境缺少官方 h5py SDK。"""

    code = "hdf5_unavailable"


class Hdf5MetadataError(Hdf5ProbeError):
    """结构元数据无法解码或自相矛盾。"""

    code = "invalid_metadata"


class Hdf5MetadataLimitError(Hdf5MetadataError):
    """数据集/组/属性数、文本长度或读取字节超过有界上限。"""

    code = "metadata_limit_exceeded"


__all__ = [
    "Hdf5InputError",
    "Hdf5MetadataError",
    "Hdf5MetadataLimitError",
    "Hdf5ProbeError",
    "Hdf5UnavailableError",
    "NotHdf5Error",
]
