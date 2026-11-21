"""HDF5 只读 Adapter：结构摘要（DDP-V21-N3-006）。

只做“读结构”，不做数据解码、转码或落盘；结果有界、错误显式。
底层使用官方 h5py。
"""

from ddp_data.hdf5.adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    DEFAULT_HDF5_ADAPTER,
    Hdf5ReadOnlyAdapter,
    detect_hdf5,
    read_hdf5_structure,
)
from ddp_data.hdf5.errors import (
    Hdf5InputError,
    Hdf5MetadataError,
    Hdf5MetadataLimitError,
    Hdf5ProbeError,
    Hdf5UnavailableError,
    NotHdf5Error,
)
from ddp_data.hdf5.metadata import (
    HDF5_SIGNATURE,
    MAX_ATTRIBUTES,
    MAX_DATASETS,
    MAX_GROUPS,
    MAX_METADATA_BYTES,
    MAX_TEXT_LENGTH,
    read_hdf5_structure_from,
)
from ddp_data.hdf5.models import (
    Hdf5DatasetSummary,
    Hdf5GroupSummary,
    Hdf5LinkSummary,
    Hdf5Structure,
)

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DEFAULT_HDF5_ADAPTER",
    "HDF5_SIGNATURE",
    "MAX_ATTRIBUTES",
    "MAX_DATASETS",
    "MAX_GROUPS",
    "MAX_METADATA_BYTES",
    "MAX_TEXT_LENGTH",
    "Hdf5DatasetSummary",
    "Hdf5GroupSummary",
    "Hdf5InputError",
    "Hdf5LinkSummary",
    "Hdf5MetadataError",
    "Hdf5MetadataLimitError",
    "Hdf5ProbeError",
    "Hdf5ReadOnlyAdapter",
    "Hdf5Structure",
    "Hdf5UnavailableError",
    "NotHdf5Error",
    "detect_hdf5",
    "read_hdf5_structure",
    "read_hdf5_structure_from",
]
