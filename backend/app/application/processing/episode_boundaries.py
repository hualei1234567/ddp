"""``EPISODE_SEGMENTATION`` 的边界推导（V31-018）。

阶段只在**输入本身携带明确边界**时创建 Episode。推导必须是纯函数：输入是
运行的对象引用与元数据阶段产出的文档，输出是边界计划——不读存储、不写库，
这样"为什么这个文件被/没被切成 Episode"可以只靠一次调用复盘。

当前承认的明确边界只有一种：**单文件单 Episode 的 MP4**。文件本身就是一个
连续录制片段，边界是整段 ``[0, duration)``；duration 来自元数据阶段的 MP4 探测
（``probe_mp4`` 的 ``duration_ms``），不是猜测。

其它格式一律记入 ``skipped`` 并写明原因：MCAP / ROS Bag / HDF5 / Parquet 都可能
在一个文件里装多段 Episode，文件的起止时间是**容器**的时间范围而不是 Episode
边界——拿它当边界会造出一个覆盖全部片段的假 Episode。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.application.processing.refs import raw_asset_id_from_ref

_NS_PER_MS = 1_000_000

#: 边界唯一承认的格式。新增格式时必须同时说明"为什么这个格式不会有多个 Episode"。
SINGLE_EPISODE_FORMAT = "MP4"


@dataclass(frozen=True, slots=True)
class EpisodeBoundary:
    """一段可创建 Episode 的明确边界（左闭右开，纳秒）。"""

    raw_object_key: str
    start_time_ns: int
    end_time_ns: int

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns


@dataclass(frozen=True, slots=True)
class BoundarySkip:
    """一段输入没有可用边界；``reason`` 是面向人的原因码。"""

    raw_object_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class BoundaryPlan:
    boundaries: tuple[EpisodeBoundary, ...]
    skipped: tuple[BoundarySkip, ...]


def plan_episode_boundaries(
    *,
    project_id: UUID,
    input_refs: Sequence[str],
    metadata_documents: Mapping[str, Mapping[str, object]],
) -> BoundaryPlan:
    """按输入顺序推导边界；同一 key 只处理一次。

    ``metadata_documents`` 以原始对象 key 为索引（元数据产物的 ``source_ref``）。
    没有该键说明元数据阶段没为这个对象产出文档——那不是"文件里没有 Episode"，
    而是"我们不知道"，因此如实记入 ``skipped``。
    """
    boundaries: list[EpisodeBoundary] = []
    skipped: list[BoundarySkip] = []
    seen: set[str] = set()

    for ref in input_refs:
        if ref in seen:
            continue
        seen.add(ref)
        if raw_asset_id_from_ref(ref, str(project_id)) is None:
            # 不是本项目的 RawAsset 对象 key（例如 canonical manifest）：它要么
            # 已关联 Episode，要么根本不是可切分的原始对象，本阶段不处理。
            continue
        document = metadata_documents.get(ref)
        if document is None:
            skipped.append(BoundarySkip(ref, "metadata_artifact_missing"))
            continue
        boundary = _boundary_from_document(ref, document)
        if isinstance(boundary, BoundarySkip):
            skipped.append(boundary)
        else:
            boundaries.append(boundary)

    return BoundaryPlan(boundaries=tuple(boundaries), skipped=tuple(skipped))


def _boundary_from_document(
    ref: str,
    document: Mapping[str, object],
) -> EpisodeBoundary | BoundarySkip:
    raw_format = document.get("format")
    if raw_format != SINGLE_EPISODE_FORMAT:
        label = raw_format if isinstance(raw_format, str) else "UNKNOWN"
        return BoundarySkip(
            ref,
            f"{label} 可能是多 Episode 容器，缺少明确的 Episode 边界声明",
        )

    payload = document.get("metadata")
    metadata = payload if isinstance(payload, Mapping) else {}
    duration_ms = metadata.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
        return BoundarySkip(ref, "duration_missing")

    end_time_ns = duration_ms * _NS_PER_MS
    return EpisodeBoundary(
        raw_object_key=ref,
        start_time_ns=0,
        end_time_ns=end_time_ns,
    )


__all__ = [
    "SINGLE_EPISODE_FORMAT",
    "BoundaryPlan",
    "BoundarySkip",
    "EpisodeBoundary",
    "plan_episode_boundaries",
]
