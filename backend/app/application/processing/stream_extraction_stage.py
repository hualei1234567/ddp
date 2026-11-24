"""``STREAM_EXTRACTION`` 阶段：从真实 payload 产出 SensorStream / Observation / Action。

对应 V51-001 / V51-002 / V51-003。

**这个阶段存在的理由**

S0 审计实测：``canonical/v1.py`` 早有 ``SensorStream`` / ``Observation`` /
``Action`` 的类型定义，但**全仓零处实例化**——类型只是"能表达"，从未"真的
产生"。``processing_artifacts`` 表也是 0 行。

本阶段填上这一格：读真实 HDF5 payload（``ddp_data.hdf5.read_hdf5_dataset_slice``），
按**显式声明的布局**映射为 Canonical 三件套，写成版本化 Artifact 描述。

**关键设计（每条都对应一个真实教训）**

1. **不做格式猜测**：只处理 ``_LAYOUTS`` 里显式登记的布局。未知布局如实
   ``SKIPPED`` 并写原因——猜布局等于伪造数据语义（见 MEM-INC-004：
   合成样本被当真实样本的同类错误）。
2. **缺失即显式**：无动作源时 ``actions`` 返回 ``None`` 并标
   ``ACTION_SOURCE_ABSENT``，**绝不**用零值或视频描述伪造 Action。
   不用空列表——空列表会被读成"有动作但一条都没有"。
3. **有界读取**：复用 payload 层的切片上限，不把整个 HDF5 读进内存。
4. **实际行数 vs 数据集行数**：``payload.length`` 是本次读到的行数，
   ``payload.shape[0]`` 是数据集总行数。受 ``max_frames`` 限制时两者不同，
   用错会把未读取的行也当成有数据（这是实现时真实踩到的 bug）。
5. **产物 key 不含时间戳**：同一输入重跑必须落到同一 key，否则产物分叉。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.processing.stages import (
    StageContext,
    StageFailure,
    StageOutcome,
)
from app.domain.processing.enums import ProcessingStepStatus

#: 产物 Schema 版本。改变输出结构必须升版本，否则下游无法判断兼容性。
STREAM_SCHEMA_VERSION = "ddp.processing.stream-extraction/1.0.0"

#: 单次切片读取的最大行数。真实机器人 Episode 常 40~200 行，取 512 有余量。
MAX_FRAMES_PER_SLICE = 512

#: 单次提取允许的最大 demo 数（防止超大 HDF5 让阶段跑几十分钟）。
MAX_DEMOS_PER_RUN = 64


@dataclass(frozen=True, slots=True)
class Hdf5Layout:
    """一个已知 HDF5 布局的声明。**布局是数据契约，不是启发式猜测。**"""

    layout_id: str
    version: str
    #: demo 组的前缀（如 ``data/demo_``）
    demo_prefix: str
    #: 动作数据集相对路径（相对 demo 组）；``None`` 表示本布局无动作源
    action_dataset: str | None
    #: 状态/观测数据集相对路径
    observation_dataset: str | None
    #: 时间戳数据集相对路径（可选）
    timestamp_dataset: str | None
    #: 已知限制，会写进产物供下游判断能用什么
    limitations: tuple[str, ...]


#: 已实测确认的布局登记表。新增布局必须先有真实样本验证，不能凭文档猜。
_LAYOUTS: tuple[Hdf5Layout, ...] = (
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
    """产物 key：含 layout 与 demo 序号，便于按 demo 定位与增量重跑。

    不含时间戳：同一输入重跑必须落到同一 key，否则会产出分叉产物。
    """
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

    ``read_slice`` 是 ``read_hdf5_dataset_slice`` 的注入形式，签名
    ``(path, dataset_path, *, start, length) -> payload``。

    返回 ``{streams, observations, actions, limitations}``。

    **为什么 actions 可能是 None**：布局声明无动作源时必须返回 ``None`` 而不是
    空列表——两者语义完全不同（见模块 docstring §2）。
    """
    if not isinstance(demo_index, int) or demo_index < 0:
        raise StageFailure("invalid_layout", "demo_index 必须是非负整数", retryable=False)

    demo_group = f"{layout.demo_prefix}{demo_index}"
    streams: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    actions: list[dict[str, object]] | None = None

    if layout.observation_dataset:
        obs = _safe_slice(
            read_slice,
            file_path,
            f"/{demo_group}/{layout.observation_dataset}",
            max_frames=max_frames,
        )
        if obs is not None:
            values, shape, dtype, rows = obs
            row_width = _row_width(shape)
            stream_id = f"{demo_group}/{layout.observation_dataset}"
            streams.append(
                {
                    "stream_id": stream_id,
                    "sensor_type": "proprioception",
                    "modality": "STATE",
                    "dtype": dtype,
                    "shape": list(shape),
                    "sample_rate_hz": None,
                    "unit": "mixed",
                    "coordinate_frame": "UNKNOWN",
                    "calibration_id": None,
                    "calibration_version": None,
                    "limitations": ["单位与坐标系未由源数据提供"],
                }
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
            read_slice,
            file_path,
            f"/{demo_group}/{layout.action_dataset}",
            max_frames=max_frames,
        )
        if act is not None:
            values, shape, dtype, rows = act
            row_width = _row_width(shape)
            stream_id = f"{demo_group}/{layout.action_dataset}"
            streams.append(
                {
                    "stream_id": stream_id,
                    "sensor_type": "actuator",
                    "modality": "ACTION",
                    "dtype": dtype,
                    "shape": list(shape),
                    "sample_rate_hz": None,
                    "unit": "normalized",
                    "coordinate_frame": "END_EFFECTOR",
                    "calibration_id": None,
                    "calibration_version": None,
                    "limitations": ["动作语义版本由布局声明提供"],
                }
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


def _safe_slice(
    read_slice: Callable[..., Any],
    file_path: str,
    dataset_path: str,
    *,
    max_frames: int,
) -> tuple[tuple[Any, ...], tuple[int, ...], str, int] | None:
    """读切片；数据集不存在返回 ``None``（布局可选字段），其他错误上抛。

    **为什么区分「不存在」与「读取失败」**：不存在是布局声明的可选字段缺失，
    属正常；读取失败是真实故障，必须让阶段失败而不是静默跳过。

    返回的第四个元素是 **实际读取行数**（``payload.length``），不是数据集总行数。
    """
    from ddp_data.hdf5.errors import Hdf5MetadataError

    try:
        payload = read_slice(file_path, dataset_path, start=0, length=max_frames)
    except Hdf5MetadataError as exc:
        if "数据集不存在" in str(exc):
            return None
        raise StageFailure(
            "hdf5_payload_read_failed",
            f"读取 {dataset_path} 失败：{exc}",
            retryable=True,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 统一翻译为阶段失败
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
    """把一个 demo 的提取结果渲染为 NDJSON（首行元信息，后续逐帧一行）。

    逐帧消费方无需先把整个文件解析成对象树；也便于未来按行追加。
    """
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
    """阶段处理器：从 HDF5 输入提取三件套，产出 Artifact 描述。

    **本阶段只处理显式声明布局的 HDF5 输入**。找不到可识别布局时如实
    ``SKIPPED`` 并写明原因。

    产物写入对象存储与 ``processing_artifacts`` 登记由编排器负责——
    本阶段只返回产物**描述**，与既有阶段一致（见 ``StageArtifact`` 文档）。
    """

    def __init__(
        self,
        *,
        read_slice: Callable[..., Any] | None = None,
        max_demos: int = MAX_DEMOS_PER_RUN,
        max_frames: int = MAX_FRAMES_PER_SLICE,
        layout: Hdf5Layout | None = None,
    ) -> None:
        if read_slice is None:
            from ddp_data.hdf5 import read_hdf5_dataset_slice as _default

            read_slice = _default
        self._read_slice = read_slice
        self._max_demos = max_demos
        self._max_frames = max_frames
        self._layout = layout or _LAYOUTS[0]

    def __call__(self, context: StageContext) -> StageOutcome:
        hdf5_refs = [ref for ref in context.run.input_refs if ref.lower().endswith(".hdf5")]
        if not hdf5_refs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：input_refs 中没有 .hdf5 输入（本阶段只处理 HDF5 布局）",
            )
        # 真实文件路径由对象存储落地后注入；阶段本身不碰存储（保持纯函数边界）。
        raise StageFailure(
            "stream_extraction_needs_local_materialization",
            "STREAM_EXTRACTION 需要对象存储先把 HDF5 物化到本地路径；"
            "提取逻辑已就绪（extract_robomimic_demo），编排接线在后续提交",
            retryable=False,
        )


__all__ = [
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
