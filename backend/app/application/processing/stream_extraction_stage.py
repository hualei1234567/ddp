"""``STREAM_EXTRACTION`` 阶段：对象存储 → 本地 HDF5 → 三件套 → 对象存储。

对应 V51-001 / V51-002 / V51-003。

**这个阶段存在的理由**

S0 审计实测：``canonical/v1.py`` 早有 ``SensorStream`` / ``Observation`` /
``Action`` 的类型定义，但**全仓零处实例化**，``processing_artifacts`` 表 0 行。
类型只是"能表达"，从未"真的产生"。

本阶段填上这一格，并把整条链打通：
对象存储里的 HDF5 →（有界下载）→ 本地临时文件 →（真实 payload 读取）→
Canonical 三件套 →（NDJSON 序列化）→ 对象存储产物 + Artifact 登记。

**关键设计（每条对应一个真实教训）**

1. **不做格式猜测**：只处理 ``_LAYOUTS`` 显式登记的布局。未知布局如实
   ``SKIPPED`` 并写原因——猜布局等于伪造数据语义（见 MEM-INC-004）。
2. **缺失即显式**：无动作源时 ``actions`` 返回 ``None`` 并标
   ``ACTION_SOURCE_ABSENT``，绝不用零值伪造 Action。
3. **有界下载**：用 ``download_to`` + ``max_bytes``，不把整个 HDF5 读进内存。
4. **实际行数 vs 数据集行数**：用 ``payload.length`` 而非 ``shape[0]``
   （见 MEM-INC-010：用错会凭空产生未读取数据的索引 = 静默数据伪造）。
5. **临时文件必清理**：``finally`` 删除，避免处理几百个 run 后磁盘打满。
6. **产物 key 不含时间戳**：同输入重跑必须落同一 key，否则产物分叉。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.application.processing.ports import InputObjectReader, StageArtifactStore
from app.application.processing.stages import (
    StageArtifact,
    StageContext,
    StageFailure,
    StageOutcome,
)
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStepStatus

#: 产物 Schema 版本。改变输出结构必须升版本，否则下游无法判断兼容性。
STREAM_SCHEMA_VERSION = "ddp.processing.stream-extraction/1.0.0"

#: 单次切片读取的最大行数。真实机器人 Episode 常 40~200 行，取 512 有余量。
MAX_FRAMES_PER_SLICE = 512

#: 单次提取允许的最大 demo 数（防止超大 HDF5 让阶段跑几十分钟）。
MAX_DEMOS_PER_RUN = 64

#: 单个 HDF5 允许的最大字节数（有界下载上限）。超过即失败而非截断。
MAX_HDF5_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Hdf5Layout:
    """一个已知 HDF5 布局的声明。**布局是数据契约，不是启发式猜测。**"""

    layout_id: str
    version: str
    demo_prefix: str
    action_dataset: str | None
    observation_dataset: str | None
    timestamp_dataset: str | None
    limitations: tuple[str, ...]


#: 已实测确认的布局登记表。新增布局必须先有真实样本验证，不能凭文档猜。
LAYOUTS: tuple[Hdf5Layout, ...] = (
    Hdf5Layout(
        layout_id="robomimic_hdf5_v1.5",
        version="1",
        demo_prefix="data/demo_",
        action_dataset="actions",
        observation_dataset="states",
        timestamp_dataset="user_info/0/timestamp",
        limitations=(
            "ph 版本不含图像观测（无 obs 组）",
            "仿真数据，无真实标定",
            "states 为 32 维低维状态，非原始传感器读数",
        ),
    ),
)


def stream_artifact_key(project_id: UUID, run_id: UUID, layout_id: str, index: int) -> str:
    """产物 key：含 layout 与 demo 序号。不含时间戳（重跑必须同 key）。"""
    return f"processing-run/{project_id}/{run_id}/streams/{layout_id}/{index:04d}.ndjson"


def extract_robomimic_demo(
    *,
    read_slice: Any,
    file_path: str,
    demo_index: int,
    layout: Hdf5Layout,
    max_frames: int = MAX_FRAMES_PER_SLICE,
) -> dict[str, object]:
    """从单个 demo 提取 Canonical 记录（纯函数，便于单测）。

    返回 ``{streams, observations, actions, limitations}``。
    ``actions`` 为 ``None`` 表示布局声明无动作源（不是"空列表"）。
    """
    if not isinstance(demo_index, int) or demo_index < 0:
        raise StageFailure("invalid_layout", "demo_index 必须是非负整数", retryable=False)

    demo_group = f"{layout.demo_prefix}{demo_index}"
    streams: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    actions: list[dict[str, object]] | None = None

    if layout.observation_dataset:
        obs = _safe_slice(
            read_slice, file_path, f"/{demo_group}/{layout.observation_dataset}", max_frames
        )
        if obs is not None:
            values, shape, dtype, rows = obs
            row_width = _row_width(shape)
            stream_id = f"{demo_group}/{layout.observation_dataset}"
            streams.append(
                _stream_record(
                    stream_id=stream_id,
                    sensor_type="proprioception",
                    modality="STATE",
                    dtype=dtype,
                    shape=shape,
                    unit="mixed",
                    frame="UNKNOWN",
                    limitation="单位与坐标系未由源数据提供",
                )
            )
            for index in range(rows):
                chunk = values[index * row_width : (index + 1) * row_width]
                observations.append(
                    {
                        "observation_id": f"{demo_group}/obs/{index}",
                        "stream_id": stream_id,
                        "frame_index": index,
                        "values": list(chunk),
                        "timestamp_ns": None,
                        "dtype": dtype,
                        "shape": list(shape[1:]),
                        "validity": "VALID",
                        "source_index": index,
                    }
                )

    if layout.action_dataset:
        act = _safe_slice(
            read_slice, file_path, f"/{demo_group}/{layout.action_dataset}", max_frames
        )
        if act is not None:
            values, shape, dtype, rows = act
            row_width = _row_width(shape)
            stream_id = f"{demo_group}/{layout.action_dataset}"
            streams.append(
                _stream_record(
                    stream_id=stream_id,
                    sensor_type="actuator",
                    modality="ACTION",
                    dtype=dtype,
                    shape=shape,
                    unit="normalized",
                    frame="END_EFFECTOR",
                    limitation="动作语义版本由布局声明提供",
                )
            )
            actions = []
            for index in range(rows):
                chunk = values[index * row_width : (index + 1) * row_width]
                actions.append(
                    {
                        "action_id": f"{demo_group}/act/{index}",
                        "frame_index": index,
                        "values": list(chunk),
                        "timestamp_ns": None,
                        "dtype": dtype,
                        "shape": list(shape[1:]),
                        "action_type": "END_EFFECTOR_DELTA",
                        "unit": "normalized",
                        "validity": "VALID",
                        "source_index": index,
                        "semantics_version": f"{layout.layout_id}/{layout.version}",
                    }
                )

    return {
        "streams": streams,
        "observations": observations,
        "actions": actions,
        "limitations": list(layout.limitations),
    }


def _stream_record(
    *,
    stream_id: str,
    sensor_type: str,
    modality: str,
    dtype: str,
    shape: tuple[int, ...],
    unit: str,
    frame: str,
    limitation: str,
) -> dict[str, object]:
    return {
        "stream_id": stream_id,
        "sensor_type": sensor_type,
        "modality": modality,
        "dtype": dtype,
        "shape": list(shape),
        "sample_rate_hz": None,
        "unit": unit,
        "coordinate_frame": frame,
        "calibration_id": None,
        "calibration_version": None,
        "limitations": [limitation],
    }


def _safe_slice(
    read_slice: Callable[..., Any],
    file_path: str,
    dataset_path: str,
    max_frames: int,
) -> tuple[tuple[Any, ...], tuple[int, ...], str, int] | None:
    """读切片；数据集不存在返回 ``None``（可选字段），其他错误上抛。

    返回第四个元素是**实际读取行数**（``payload.length``），不是数据集总行数。
    见 MEM-INC-010：用 ``shape[0]`` 迭代会凭空产生未读取数据的索引。
    """
    from ddp_data.hdf5.errors import Hdf5MetadataError

    try:
        payload = read_slice(file_path, dataset_path, start=0, length=max_frames)
    except Hdf5MetadataError as exc:
        if "数据集不存在" in str(exc):
            return None
        raise StageFailure(
            "hdf5_payload_read_failed", f"读取 {dataset_path} 失败：{exc}", retryable=True
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(
            "hdf5_payload_read_failed",
            f"读取 {dataset_path} 失败：{type(exc).__name__}",
            retryable=True,
        ) from exc
    return payload.values, payload.shape, payload.dtype, payload.length


def _row_width(shape: tuple[int, ...]) -> int:
    width = 1
    for dim in shape[1:]:
        width *= int(dim)
    return width


def render_ndjson(record: dict[str, object]) -> bytes:
    """把一个 demo 的提取结果渲染为 NDJSON（首行 meta + 逐帧一行）。"""
    header = {
        "_meta": {
            "schema_version": STREAM_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "streams": record.get("streams", []),
            "limitations": record.get("limitations", []),
            "action_source": (
                "PRESENT" if record.get("actions") is not None else "ACTION_SOURCE_ABSENT"
            ),
        }
    }
    lines = [json.dumps(header, ensure_ascii=False, sort_keys=True)]
    for item in record.get("observations", []):  # type: ignore[union-attr]
        lines.append(
            json.dumps({"type": "observation", **item}, ensure_ascii=False, sort_keys=True)
        )
    for item in record.get("actions") or []:
        lines.append(json.dumps({"type": "action", **item}, ensure_ascii=False, sort_keys=True))
    return ("\n".join(lines) + "\n").encode("utf-8")


def payload_sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class StreamExtractionHandler:
    """阶段处理器：HDF5 输入 → 三件套 NDJSON 产物。

    输入判定：``context.run.input_refs`` 里以 ``.hdf5`` 结尾的对象。
    无 HDF5 输入时如实 ``SKIPPED``（不是失败——这个 run 可能只处理 MP4）。
    """

    def __init__(
        self,
        reader: InputObjectReader,
        artifacts: StageArtifactStore,
        *,
        read_slice: Callable[..., Any] | None = None,
        layout: Hdf5Layout | None = None,
        max_demos: int = MAX_DEMOS_PER_RUN,
        max_frames: int = MAX_FRAMES_PER_SLICE,
        max_hdf5_bytes: int = MAX_HDF5_BYTES,
    ) -> None:
        if read_slice is None:
            from ddp_data.hdf5 import read_hdf5_dataset_slice as _default

            read_slice = _default
        self._reader = reader
        self._artifacts = artifacts
        self._read_slice = read_slice
        self._layout = layout or LAYOUTS[0]
        self._max_demos = max_demos
        self._max_frames = max_frames
        self._max_hdf5_bytes = max_hdf5_bytes

    def __call__(self, context: StageContext) -> StageOutcome:
        hdf5_refs = [ref for ref in context.run.input_refs if ref.lower().endswith(".hdf5")]
        if not hdf5_refs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：input_refs 中没有 .hdf5 输入（本阶段只处理 HDF5 布局）",
            )

        ref = hdf5_refs[0]
        project_id = context.run.project_id
        run_id = context.run.run_id

        with tempfile.TemporaryDirectory(prefix="ddp-stream-") as tmpdir:
            local = Path(tmpdir) / "input.hdf5"
            try:
                self._reader.download_to(ref, local, max_bytes=self._max_hdf5_bytes)
            except Exception as exc:  # noqa: BLE001 - 统一翻译为阶段失败
                raise StageFailure(
                    "hdf5_download_failed",
                    f"下载 {ref} 失败：{type(exc).__name__}",
                    retryable=True,
                ) from exc

            demo_count = self._detect_demo_count(str(local))
            if demo_count == 0:
                return StageOutcome(
                    status=ProcessingStepStatus.SKIPPED.value,
                    log_summary=f"跳过：{ref} 中未找到 {self._layout.demo_prefix}* 布局",
                )

            total = min(demo_count, self._max_demos)
            artifact_descs: list[StageArtifact] = []
            output_refs: list[str] = []
            obs_total = act_total = 0

            for index in range(total):
                record = extract_robomimic_demo(
                    read_slice=self._read_slice,
                    file_path=str(local),
                    demo_index=index,
                    layout=self._layout,
                    max_frames=self._max_frames,
                )
                if not record["observations"] and record["actions"] is None:
                    continue
                data = render_ndjson(record)
                key = stream_artifact_key(project_id, run_id, self._layout.layout_id, index)
                self._artifacts.put_bytes(key, data)
                artifact_descs.append(
                    StageArtifact(
                        kind=ProcessingArtifactKind.STREAM_EXTRACTION,
                        object_key=key,
                        schema_version=STREAM_SCHEMA_VERSION,
                        content_type="application/x-ndjson",
                        size_bytes=len(data),
                        checksum_sha256=payload_sha256(data).removeprefix("sha256:"),
                        source_ref=ref,
                    )
                )
                output_refs.append(key)
                obs_total += len(record["observations"])  # type: ignore[arg-type]
                act_total += len(record["actions"] or [])  # type: ignore[arg-type]

        if not artifact_descs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=f"跳过：{ref} 的 {total} 个 demo 均无可用观测/动作数据",
            )

        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"提取 {len(artifact_descs)} 个 demo（layout={self._layout.layout_id}）："
                f"observation {obs_total} 条，action {act_total} 条"
            ),
            output_refs=tuple(output_refs),
            artifacts=tuple(artifact_descs),
        )

    def _detect_demo_count(self, file_path: str) -> int:
        """探测 ``data/demo_N`` 数量。用 h5py 直接读，不猜。

        ``demo_prefix`` 形如 ``data/demo_``：父组是 ``data``，成员名是 ``demo_N``。
        只数**名字匹配前缀且确为组**的成员——把其它数据集误算进来会产生
        指向不存在 demo 的索引（那正是 MEM-INC-010 的同类错误）。
        """
        try:
            import h5py
        except Exception:  # noqa: BLE001 - 无 SDK 时按 0 处理（阶段会 SKIPPED）
            return 0
        prefix = self._layout.demo_prefix
        parent_path, _, name_prefix = prefix.rpartition("/")
        if not parent_path or not name_prefix:
            return 0
        try:
            with h5py.File(file_path, "r") as handle:
                if parent_path not in handle:
                    return 0
                parent = handle[parent_path]
                return sum(
                    1
                    for name in parent
                    if name.startswith(name_prefix) and isinstance(parent[name], h5py.Group)
                )
        except Exception:  # noqa: BLE001 - 损坏文件时按 0 处理，后续阶段会报告
            return 0


__all__ = [
    "LAYOUTS",
    "MAX_DEMOS_PER_RUN",
    "MAX_FRAMES_PER_SLICE",
    "STREAM_SCHEMA_VERSION",
    "Hdf5Layout",
    "StreamExtractionHandler",
    "extract_robomimic_demo",
    "payload_sha256",
    "render_ndjson",
    "stream_artifact_key",
]
