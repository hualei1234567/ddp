"""``METADATA_EXTRACTION`` / ``FORMAT_PARSING`` 阶段（DDP-V21-N4-002 增量）。

**只读、有界、可追溯**

- 只读对象元数据：MP4 走受控 ffprobe（``ddp_data.media.mp4``），Parquet 走
  官方 PyArrow footer 读取（``ddp_data.parquet``），MCAP 走官方 SDK summary
  读取（``ddp_data.mcap``）。三者都只触碰文件头/尾/footer，不把对象本体读入
  内存；对象先流式落盘到临时目录，且受 ``max_object_bytes`` 上限约束。
- 解析器按**内容魔数**选择（``domain.ingestion.validation.detect_format``），
  不信任扩展名；无法识别时显式失败或显式 ``SKIPPED`` 并写明原因，绝不默认成功。
- 产物是服务端生成的 JSON 对象（``processing-run/<project>/<run>/metadata/...``），
  内容有界、不含绝对路径与凭据；``output_refs`` 可逐个回查。

**不伪造 Episode**

孤立 MP4/Parquet/MCAP 没有 Episode 标识、指令文本与媒体资产关联。
``FORMAT_PARSING`` 只在 MP4 上投影出既有 Canonical ``VideoStream``；
Parquet/MCAP 只产出“阶段产物引用”并显式标注 ``canonical_episode=false``，
不编造 Episode 字段（N3-003 既定决定）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import UUID

from app.application.processing.ports import (
    InputObjectReader,
    ObjectInspectionError,
    ObjectMissingError,
    ObjectTooLargeError,
    StageArtifactStore,
)
from app.application.processing.stages import (
    StageContext,
    StageFailure,
    StageOutcome,
    is_project_scoped,
)
from app.domain.ingestion.enums import RawAssetFormat
from app.domain.ingestion.validation import HDF5_DETECTION_BYTES, detect_format
from app.domain.processing.enums import ProcessingStepStatus

#: 元数据产物自身的版本；与 Canonical 版本解耦，只约束本阶段的 JSON 结构。
METADATA_SCHEMA_VERSION = "ddp.processing.metadata/1.0.0"
#: 阶段产物引用的版本；用于 Parquet/MCAP 这类没有 Canonical Episode 语义的流。
REFERENCE_SCHEMA_VERSION = "ddp.processing.stage-artifact/1.0.0"

#: 单次运行最多提取元数据的对象数。运行 ``output_refs`` 上限 1000，
#: 本阶段每个对象产出一条 metadata 引用、FORMAT_PARSING 再产出一条 canonical
#: 引用；200 同时给 QUALITY 阶段的引用留出余量。超限显式失败，不静默截断。
MAX_METADATA_OBJECTS = 200

#: 单个对象允许落盘的上限。ffprobe/PyArrow/mcap 只读文件头尾，但需要本地
#: 文件；超过该上限显式失败，避免把超大对象无界地复制到 Worker 临时盘。
DEFAULT_MAX_OBJECT_BYTES = 2 * 1024**3

#: 单个阶段产物的字节上限；元数据摘要必须是有界的。
DEFAULT_MAX_ARTIFACT_BYTES = 256 * 1024

#: 内容签名探测只读文件头尾的小片段，不读取对象本体。
_DETECTION_PREFIX_BYTES = HDF5_DETECTION_BYTES
_DETECTION_SUFFIX_BYTES = 16

#: Parquet 叶子列上限；超过即视为元数据超限，显式失败。
MAX_PARQUET_COLUMNS = 512

#: 本阶段支持的格式；其余格式显式失败或跳过，不默认成功。
SUPPORTED_FORMATS = frozenset(
    {RawAssetFormat.MP4, RawAssetFormat.PARQUET, RawAssetFormat.MCAP, RawAssetFormat.HDF5}
)

MP4_ADAPTER_ID = "mp4.ffprobe_readonly"
MP4_ADAPTER_VERSION = "1.0.0"


def metadata_artifact_key(project_id: UUID, run_id: UUID, index: int, raw_format: str) -> str:
    """服务端生成的元数据产物 Key；不含客户端路径与文件名。"""
    return (
        f"processing-run/{project_id}/{run_id}/metadata/" f"{index:04d}-{raw_format.lower()}.json"
    )


def canonical_artifact_key(
    project_id: UUID,
    run_id: UUID,
    index: int,
    *,
    kind: str,
) -> str:
    """服务端生成的 Canonical 产物 Key；``kind`` 只允许固定短名。"""
    return f"processing-run/{project_id}/{run_id}/canonical/{index:04d}-{kind}.json"


class MetadataExtractionHandler:
    """对 ``input_refs`` 指向的对象做只读元数据提取。"""

    def __init__(
        self,
        reader: InputObjectReader,
        artifacts: StageArtifactStore,
        *,
        max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        max_objects: int = MAX_METADATA_OBJECTS,
    ) -> None:
        if max_object_bytes <= 0 or max_artifact_bytes <= 0 or max_objects <= 0:
            raise ValueError("元数据提取上限必须为正整数")
        self._reader = reader
        self._artifacts = artifacts
        self._max_object_bytes = max_object_bytes
        self._max_artifact_bytes = max_artifact_bytes
        self._max_objects = max_objects

    def __call__(self, context: StageContext) -> StageOutcome:
        refs = context.run.input_refs
        project_id = str(context.run.project_id)
        # 领域层保证 input_refs 非空；这里只对数量上限做防御。
        if len(refs) > self._max_objects:
            raise StageFailure(
                "metadata_object_limit_exceeded",
                f"本次运行包含 {len(refs)} 个输入对象，超过元数据提取上限 " f"{self._max_objects}",
                retryable=False,
            )

        # 先完成全部只读探测，再统一写产物：任何一个对象失败都不会留下
        # "部分已提取"的孤儿对象。
        extracted: list[tuple[int, str, RawAssetFormat, int, str, str, dict[str, object]]] = []
        unsupported: list[str] = []
        for index, ref in enumerate(refs):
            if not is_project_scoped(ref, project_id):
                raise StageFailure(
                    "input_ref_not_project_scoped",
                    "输入引用必须属于当前项目",
                    retryable=False,
                )
            size = self._object_size(ref)
            if size is None:
                raise StageFailure("object_missing", f"输入对象不存在：{ref}", retryable=False)
            if size == 0:
                raise StageFailure(
                    "object_empty",
                    f"输入对象为空，无法提取元数据：{ref}",
                    retryable=False,
                )
            if size > self._max_object_bytes:
                raise StageFailure(
                    "object_exceeds_limit",
                    f"输入对象 {size} 字节超过元数据提取上限 {self._max_object_bytes}",
                    retryable=False,
                )
            raw_format = self._detect(ref, size)
            if raw_format not in SUPPORTED_FORMATS:
                unsupported.append(raw_format.value if raw_format is not None else "UNKNOWN")
                continue
            adapter_id, adapter_version, payload = self._extract(ref, raw_format)
            extracted.append((index, ref, raw_format, size, adapter_id, adapter_version, payload))

        if not extracted:
            formats = "、".join(sorted(set(unsupported))) or "无"
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=(
                    "跳过：输入中没有可解析格式" f"（支持 MP4/Parquet/MCAP/HDF5，实际为 {formats}）"
                ),
            )
        if unsupported:
            formats = "、".join(sorted(set(unsupported)))
            raise StageFailure(
                "metadata_format_unsupported",
                f"存在不支持的对象格式：{formats}（支持 MP4/Parquet/MCAP/HDF5）",
                retryable=False,
            )

        output_refs = self._write_artifacts(context, extracted)
        counts = _format_counts(item[2] for item in extracted)
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"提取 {len(extracted)} 个对象元数据（{counts}），" f"产物 {len(output_refs)} 个"
            ),
            output_refs=output_refs,
        )

    def _object_size(self, ref: str) -> int | None:
        try:
            return self._reader.object_size(ref)
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法读取输入元数据",
            ) from exc

    def _detect(self, ref: str, size: int) -> RawAssetFormat | None:
        prefix_length = min(size, _DETECTION_PREFIX_BYTES)
        suffix_length = min(size, _DETECTION_SUFFIX_BYTES)
        try:
            prefix = self._reader.read_range(ref, start=0, length=prefix_length)
            suffix = self._reader.read_range(
                ref,
                start=max(0, size - suffix_length),
                length=suffix_length,
            )
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法探测对象格式",
            ) from exc
        return detect_format(prefix, suffix)

    def _extract(
        self,
        ref: str,
        raw_format: RawAssetFormat,
    ) -> tuple[str, str, dict[str, object]]:
        with tempfile.TemporaryDirectory(prefix="ddp-metadata-") as tmp:
            path = Path(tmp) / "object.bin"
            try:
                self._reader.download_to(ref, path, max_bytes=self._max_object_bytes)
            except ObjectMissingError as exc:
                raise StageFailure(
                    "object_missing",
                    f"输入对象不存在：{ref}",
                    retryable=False,
                ) from exc
            except ObjectTooLargeError as exc:
                raise StageFailure(
                    "object_exceeds_limit",
                    f"输入对象超过元数据提取上限 {self._max_object_bytes}",
                    retryable=False,
                ) from exc
            except ObjectInspectionError as exc:
                raise StageFailure(
                    "object_storage_unavailable",
                    "对象存储不可用，无法下载输入对象",
                ) from exc
            return _extract_payload(raw_format, path)

    def _write_artifacts(
        self,
        context: StageContext,
        extracted: list[tuple[int, str, RawAssetFormat, int, str, str, dict[str, object]]],
    ) -> tuple[str, ...]:
        output_refs: list[str] = []
        for index, ref, raw_format, size, adapter_id, adapter_version, payload in extracted:
            key = metadata_artifact_key(
                context.run.project_id,
                context.run.run_id,
                index,
                raw_format.value,
            )
            document: dict[str, object] = {
                "schema_version": METADATA_SCHEMA_VERSION,
                "source_ref": ref,
                "format": raw_format.value,
                "adapter_id": adapter_id,
                "adapter_version": adapter_version,
                "object_size_bytes": size,
                "metadata": payload,
            }
            try:
                self._artifacts.put_bytes(
                    key,
                    _serialize(document, max_bytes=self._max_artifact_bytes),
                )
            except ObjectInspectionError as exc:
                raise StageFailure(
                    "object_storage_unavailable",
                    "对象存储不可用，无法写入元数据产物",
                ) from exc
            output_refs.append(key)
        return tuple(output_refs)


class FormatParsingHandler:
    """把元数据产物投影为既有 Canonical 只读结构或阶段产物引用。"""

    def __init__(
        self,
        artifacts: StageArtifactStore,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        if max_artifact_bytes <= 0:
            raise ValueError("产物读取上限必须为正整数")
        self._artifacts = artifacts
        self._max_artifact_bytes = max_artifact_bytes

    def __call__(self, context: StageContext) -> StageOutcome:
        project_id = str(context.run.project_id)
        run_id = context.run.run_id
        prefix = f"processing-run/{project_id}/{run_id}/metadata/"
        metadata_refs = [ref for ref in context.run.output_refs if ref.startswith(prefix)]
        if not metadata_refs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=("跳过：无元数据产物可转换（依赖 METADATA_EXTRACTION 阶段产出）"),
            )

        output_refs: list[str] = []
        video_count = 0
        reference_count = 0
        for index, ref in enumerate(metadata_refs):
            document = _load_metadata_artifact(
                self._artifacts,
                ref,
                max_bytes=self._max_artifact_bytes,
            )
            raw_format = document["format"]
            source_ref = document["source_ref"]
            if raw_format == RawAssetFormat.MP4.value:
                artifact = _video_stream_document(source_ref, document)
                key = canonical_artifact_key(
                    context.run.project_id,
                    run_id,
                    index,
                    kind="video-stream",
                )
                video_count += 1
            else:
                artifact = {
                    "schema_version": REFERENCE_SCHEMA_VERSION,
                    "kind": f"{raw_format}_STAGE_ARTIFACT_REFERENCE",
                    "source_ref": source_ref,
                    "metadata_ref": ref,
                    "canonical_episode": False,
                    "reason": "孤立表/消息流没有 Episode 语义，不伪造 Episode（N3-003）",
                }
                key = canonical_artifact_key(
                    context.run.project_id,
                    run_id,
                    index,
                    kind="reference",
                )
                reference_count += 1
            try:
                self._artifacts.put_bytes(
                    key,
                    _serialize(artifact, max_bytes=self._max_artifact_bytes),
                )
            except ObjectInspectionError as exc:
                raise StageFailure(
                    "object_storage_unavailable",
                    "对象存储不可用，无法写入格式解析产物",
                ) from exc
            output_refs.append(key)

        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"格式解析：投影 {video_count} 个 Canonical VideoStream，"
                f"{reference_count} 个表/消息流保留阶段产物引用（未伪造 Episode）"
            ),
            output_refs=tuple(output_refs),
        )


def _format_counts(formats: object) -> str:
    counts: dict[str, int] = {}
    for item in formats:  # type: ignore[union-attr]
        counts[item.value] = counts.get(item.value, 0) + 1
    return "、".join(f"{name} {count}" for name, count in sorted(counts.items()))


def _serialize(document: dict[str, object], *, max_bytes: int) -> bytes:
    data = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(data) > max_bytes:
        raise StageFailure(
            "metadata_artifact_too_large",
            f"元数据产物 {len(data)} 字节超过上限 {max_bytes}",
            retryable=False,
        )
    return data


def _load_metadata_artifact(
    artifacts: StageArtifactStore,
    ref: str,
    *,
    max_bytes: int,
) -> dict[str, object]:
    try:
        data = artifacts.read_bytes(ref, max_bytes=max_bytes)
    except ObjectMissingError as exc:
        raise StageFailure(
            "metadata_artifact_missing",
            "元数据产物不存在，可重试",
            retryable=True,
        ) from exc
    except ObjectInspectionError as exc:
        raise StageFailure(
            "object_storage_unavailable",
            "对象存储不可用，无法读取元数据产物",
            retryable=True,
        ) from exc
    except ObjectTooLargeError as exc:
        raise StageFailure(
            "metadata_artifact_too_large",
            "元数据产物超过读取上限",
            retryable=False,
        ) from exc
    try:
        document = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StageFailure(
            "metadata_artifact_invalid",
            "元数据产物不是合法 JSON",
            retryable=False,
        ) from exc
    if not isinstance(document, dict):
        raise StageFailure(
            "metadata_artifact_invalid",
            "元数据产物必须是 JSON 对象",
            retryable=False,
        )
    if document.get("schema_version") != METADATA_SCHEMA_VERSION:
        raise StageFailure(
            "metadata_artifact_invalid",
            "元数据产物 schema_version 不受支持",
            retryable=False,
        )
    if document.get("format") not in {item.value for item in SUPPORTED_FORMATS}:
        raise StageFailure(
            "metadata_artifact_invalid",
            "元数据产物包含不支持的格式",
            retryable=False,
        )
    if not isinstance(document.get("source_ref"), str):
        raise StageFailure(
            "metadata_artifact_invalid",
            "元数据产物缺少 source_ref",
            retryable=False,
        )
    return document


def _video_stream_document(
    source_ref: str,
    document: dict[str, object],
) -> dict[str, object]:
    """把 MP4 元数据投影为既有 Canonical ``VideoStream``。"""
    from ddp_data.canonical.v1 import (
        VIDEO_STREAM_FIELDS,
        VideoStream,
        declared_missing_fields,
    )

    payload = document.get("metadata")
    metadata = payload if isinstance(payload, dict) else {}
    duration_ms = metadata.get("duration_ms")
    values: dict[str, object] = {
        "stream_id": f"{source_ref}:video:0",
        "media_format": "video/mp4",
        "storage_reference": source_ref,
        "duration_ns": (
            int(duration_ms) * 1_000_000
            if isinstance(duration_ms, int) and not isinstance(duration_ms, bool)
            else None
        ),
        "fps": metadata.get("fps"),
        "frame_count": metadata.get("frame_count"),
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "size_bytes": document.get("object_size_bytes"),
        "encoding": metadata.get("video_codec"),
        # 以下字段孤立 MP4 无法提供；如实登记为缺失，不补默认值。
        "camera_name": None,
        "camera_name_raw": None,
        "modality": None,
        "excerpt_start_ns": None,
        "checksum": None,
        "dropped_count": None,
    }
    stream = VideoStream(
        **values,
        missing_fields=declared_missing_fields(VIDEO_STREAM_FIELDS, values),
    )
    return stream.to_dict()


def _extract_payload(
    raw_format: RawAssetFormat,
    path: Path,
) -> tuple[str, str, dict[str, object]]:
    if raw_format is RawAssetFormat.MP4:
        return _extract_mp4(path)
    if raw_format is RawAssetFormat.PARQUET:
        return _extract_parquet(path)
    if raw_format is RawAssetFormat.MCAP:
        return _extract_mcap(path)
    return _extract_hdf5(path)


def _extract_mp4(path: Path) -> tuple[str, str, dict[str, object]]:
    from ddp_data.media import MediaProbeError, probe_mp4

    try:
        result = probe_mp4(path)
    except MediaProbeError as exc:
        raise _probe_failure("MP4", exc) from exc
    return MP4_ADAPTER_ID, MP4_ADAPTER_VERSION, result.as_metadata()


def _extract_parquet(path: Path) -> tuple[str, str, dict[str, object]]:
    from ddp_data.parquet import ParquetProbeError, summarize_parquet

    try:
        preview = summarize_parquet(path)
    except ParquetProbeError as exc:
        raise _probe_failure("Parquet", exc) from exc
    return preview.adapter_id, preview.adapter_version, _parquet_payload(preview)


def _extract_mcap(path: Path) -> tuple[str, str, dict[str, object]]:
    from ddp_data.mcap import ADAPTER_ID, ADAPTER_VERSION, McapProbeError, summarize_mcap

    try:
        metadata = summarize_mcap(path)
    except McapProbeError as exc:
        raise _probe_failure("MCAP", exc) from exc
    return ADAPTER_ID, ADAPTER_VERSION, metadata.as_metadata()


def _extract_hdf5(path: Path) -> tuple[str, str, dict[str, object]]:
    from ddp_data.hdf5 import (
        ADAPTER_ID,
        ADAPTER_VERSION,
        Hdf5ProbeError,
        read_hdf5_structure,
    )

    from app.application.processing.hdf5_metadata import hdf5_stage_payload

    try:
        structure = read_hdf5_structure(path)
    except Hdf5ProbeError as exc:
        raise _probe_failure("HDF5", exc) from exc
    return ADAPTER_ID, ADAPTER_VERSION, hdf5_stage_payload(structure)


def _parquet_payload(preview: object) -> dict[str, object]:
    columns = preview.columns  # type: ignore[attr-defined]
    if len(columns) > MAX_PARQUET_COLUMNS:
        raise StageFailure(
            "metadata_limit_exceeded",
            f"Parquet 叶子列数 {len(columns)} 超过上限 {MAX_PARQUET_COLUMNS}",
            retryable=False,
        )
    return {
        "adapter_id": preview.adapter_id,  # type: ignore[attr-defined]
        "adapter_version": preview.adapter_version,  # type: ignore[attr-defined]
        "layout": preview.layout.value,  # type: ignore[attr-defined]
        "num_rows": preview.num_rows,  # type: ignore[attr-defined]
        "row_group_count": preview.row_group_count,  # type: ignore[attr-defined]
        "created_by": preview.created_by,  # type: ignore[attr-defined]
        "top_level_fields": list(preview.top_level_fields),  # type: ignore[attr-defined]
        "columns": [
            {
                "name": column.name,
                "path": column.path,
                "physical_type": column.physical_type,
                "logical_type": column.logical_type,
                "converted_type": column.converted_type,
                "repetition": column.repetition,
            }
            for column in columns
        ],
        "missing_fields": sorted(preview.missing_fields),  # type: ignore[attr-defined]
        "missing_required_fields": sorted(preview.missing_required_fields),  # type: ignore[attr-defined]
        "unmapped_columns": sorted(preview.unmapped_columns),  # type: ignore[attr-defined]
    }


def _probe_failure(raw_format: str, exc: Exception) -> StageFailure:
    """把解析器异常映射为稳定失败码；摘要只含格式与错误码，不回显路径。"""
    code = str(getattr(exc, "code", "probe_failed"))
    if code in {"ffprobe_unavailable", "mcap_unavailable", "hdf5_unavailable"}:
        return StageFailure(
            "metadata_probe_unavailable",
            f"{raw_format} 元数据探测不可用（{code}）",
            retryable=True,
        )
    if code == "probe_timeout":
        return StageFailure(
            "metadata_probe_timeout",
            f"{raw_format} 元数据探测超时（{code}）",
            retryable=True,
        )
    if code in {"metadata_limit_exceeded", "probe_output_too_large"}:
        return StageFailure(
            "metadata_limit_exceeded",
            f"{raw_format} 元数据超过有界上限（{code}）",
            retryable=False,
        )
    return StageFailure(
        "metadata_probe_failed",
        f"{raw_format} 元数据解析失败（{code}）",
        retryable=False,
    )


__all__ = [
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MAX_OBJECT_BYTES",
    "MAX_METADATA_OBJECTS",
    "MAX_PARQUET_COLUMNS",
    "METADATA_SCHEMA_VERSION",
    "MP4_ADAPTER_ID",
    "MP4_ADAPTER_VERSION",
    "REFERENCE_SCHEMA_VERSION",
    "SUPPORTED_FORMATS",
    "FormatParsingHandler",
    "MetadataExtractionHandler",
    "canonical_artifact_key",
    "metadata_artifact_key",
]
