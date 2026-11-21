"""HDF5 user block 在三个生产格式识别调用点的一致性回归（RK-63）。"""

from __future__ import annotations

from app.application.datasources.import_runner import _detect_raw_format
from app.application.ingestion.upload_completion import UploadCompletionService
from app.application.processing.metadata_stage import MetadataExtractionHandler
from app.domain.ingestion.enums import RawAssetFormat

from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
_USER_BLOCK_BYTES = 1024 * 1024
_HDF5_WITH_USER_BLOCK = b"\x00" * _USER_BLOCK_BYTES + _HDF5_MAGIC + b"payload"


class _RangeStorage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls: list[tuple[int, int]] = []

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        self.calls.append((start, length))
        return self.data[start : start + length]


def test_upload_completion_reads_bounded_hdf5_detection_window() -> None:
    storage = _RangeStorage(_HDF5_WITH_USER_BLOCK)
    service = object.__new__(UploadCompletionService)
    service._storage = storage  # type: ignore[attr-defined]

    detected = service._detect_content("raw/object.h5", len(_HDF5_WITH_USER_BLOCK))

    assert detected is RawAssetFormat.HDF5
    assert storage.calls[0] == (0, _USER_BLOCK_BYTES + len(_HDF5_MAGIC))


def test_data_source_import_uses_complete_bounded_hdf5_prefix() -> None:
    detected = _detect_raw_format(
        data=_HDF5_WITH_USER_BLOCK,
        content_type="application/octet-stream",
    )

    assert detected is RawAssetFormat.HDF5


def test_metadata_stage_reads_bounded_hdf5_detection_window() -> None:
    ref = "raw/project/content.h5"
    reader = FakeObjectReader({ref: _HDF5_WITH_USER_BLOCK})
    handler = MetadataExtractionHandler(reader, FakeArtifactStore())

    detected = handler._detect(ref, len(_HDF5_WITH_USER_BLOCK))

    assert detected is RawAssetFormat.HDF5
    assert reader.range_calls[0] == (ref, 0, _USER_BLOCK_BYTES + len(_HDF5_MAGIC))
