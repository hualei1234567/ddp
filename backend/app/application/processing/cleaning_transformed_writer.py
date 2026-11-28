"""衍生清洗流产物（TRANSFORM 结果）的 NDJSON 写入器（V61-015）。

## 为什么单独成模块

``DataCleaningHandler`` 已接近 500 行阈值，而「把衍生切片序列化成 NDJSON 并
登记为阶段产物」是一个**自包含**的职责：它只依赖 ``TransformedSlice`` 与
``StageArtifactStore``，与规则评估、报告组装无关。抽出来后：

- 处理器只负责编排（选输入 → 评估 → 写产物 → 汇总）；
- 序列化格式（行式 JSON、``_meta`` 首行、排序键）只有一处权威实现，
  将来格式升级不必在处理器里翻找。

## 红线

- **只写衍生对象**：Key 由 ``cleaning_transformed_key`` 服务端生成，
  永不覆盖原始资产或上游 ``STREAM_EXTRACTION`` 产物；
- **确定性序列化**：``sort_keys=True`` + 固定行序（observations 先、actions 后），
  同输入重跑得到字节级相同的产物与 SHA256。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from app.application.processing.ports import (
    ObjectInspectionError,
    StageArtifactStore,
)
from app.application.processing.stages import StageArtifact, StageFailure
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.cleaning_actions import TRANSFORM_SCHEMA_VERSION, TransformedSlice


def cleaning_transformed_key(project_id: UUID, run_id: UUID, index: int) -> str:
    """服务端生成的衍生 TRANSFORM 流产物 Key（不覆盖原始资产与上游流）。"""
    return f"processing-run/{project_id}/{run_id}/cleaning/transformed-{index:03d}.ndjson"


def _meta_line(item: TransformedSlice) -> str:
    return json.dumps(
        {
            "_meta": {
                "schema_version": TRANSFORM_SCHEMA_VERSION,
                "source_target_ref": item.target_ref,
                "provenance": item.transform_provenance,
            }
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _frame_lines(item: TransformedSlice) -> list[str]:
    lines: list[str] = []
    for obs in item.observations:
        lines.append(
            json.dumps(
                {
                    "type": "observation",
                    "observation_id": obs.record_id,
                    "frame_index": obs.frame_index,
                    "timestamp_ns": obs.timestamp_ns,
                    "values": list(obs.values),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    for act in item.actions:
        lines.append(
            json.dumps(
                {
                    "type": "action",
                    "action_id": act.record_id,
                    "frame_index": act.frame_index,
                    "timestamp_ns": act.timestamp_ns,
                    "values": list(act.values),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return lines


def serialize_transformed_slice(item: TransformedSlice) -> bytes:
    """把一条衍生切片序列化为 NDJSON 字节（首行 ``_meta``，其后逐帧）。"""
    lines = [_meta_line(item), *_frame_lines(item)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_transformed_slices(
    artifacts: StageArtifactStore,
    *,
    project_id: UUID,
    run_id: UUID,
    transformed_slices: Sequence[TransformedSlice],
) -> list[StageArtifact]:
    """写入全部衍生切片并返回产物描述；对象存储不可用时抛可重试 ``StageFailure``。"""
    written: list[StageArtifact] = []
    for index, item in enumerate(transformed_slices):
        ref = cleaning_transformed_key(project_id, run_id, index)
        data = serialize_transformed_slice(item)
        try:
            artifacts.put_bytes(ref, data)
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法写入衍生清洗流产物",
                retryable=True,
            ) from exc
        written.append(
            StageArtifact(
                kind=ProcessingArtifactKind.CANONICAL,
                object_key=ref,
                schema_version=TRANSFORM_SCHEMA_VERSION,
                content_type="application/x-ndjson",
                size_bytes=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                source_ref=item.source_ref,
            )
        )
    return written


__all__ = [
    "cleaning_transformed_key",
    "serialize_transformed_slice",
    "write_transformed_slices",
]
