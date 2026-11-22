"""具身事件图（embodied_event_graph_v1）→ DDP AI 建议载荷适配器。

**来源与对齐**

输入是参考实现 ``interaction-auto-labeler-v3/event_graph.py`` 的
``EpisodeEventGraph.to_dict()`` 产物（JSON）：entities / observations /
segments（task/subtask/event 三级）/ events（交互事件）/
language（三级描述），全部带 ``confidence`` 与 ``review_state``。

**输出**：DDP ``POST .../ai-suggestions`` 的 ``suggestions`` 载荷行。
映射规则（对齐蓝图 09 §6「AI 建议与人工真值」）：

- ``model_id`` / ``model_version`` / ``prompt_version`` 必填（DDP 契约）——
  由适配器配置给出，事件图本身不承载模型身份；
- ``label_key`` 用 ``<级别>.<标签名>`` 命名空间（``segment.task`` /
  ``event.<type>`` / ``language.task``），避免与人工 Schema 键冲突；
- ``suggested_value`` 是**完整片段证据**：起止帧 + 置信度 + 引用
  （event_ids/entity_ids/observation_ids），供工作台审看与采纳决策；
- ``confidence`` 透传（DDP 契约 [0,1]）；``review_state != "auto"`` 的
  条目跳过（已被人工决定，不属于"建议"）；
- 帧号 → 纳秒：事件图只有 ``fps`` 与 ``frame_count``，没有物理时钟，
  转换用 ``frame / fps * 1e9`` 并在载荷里保留 ``frame_span`` 原文，
  由调用方（backend）决定是否映射到 Episode 的真实时域。**不得虚构
  精确时间戳语义**——这里只是展示层的相对位置。

纯函数、无 IO：调用方负责文件读取、去重与幂等键。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = "embodied_event_graph_v1"
SEGMENT_LEVELS = ("task", "subtask", "event")

#: 事件图允许的复核状态中，只有 auto（尚未人工决定）才作为"建议"灌入。
SUGGESTABLE_REVIEW_STATES = frozenset({"auto"})

_NS_PER_SECOND = 1_000_000_000


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限数")
    return result


def _frames_to_ns(frame: object, fps: float) -> int:
    return int(round(float(frame) / fps * _NS_PER_SECOND))


def _span_to_value(span: Mapping[str, Any], fps: float) -> dict[str, object]:
    start_frame = span.get("start_frame")
    end_frame = span.get("end_frame")
    if start_frame is None or end_frame is None:
        raise ValueError("span 必须含 start_frame/end_frame")
    return {
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "start_time_ns": _frames_to_ns(start_frame, fps),
        "end_time_ns": _frames_to_ns(end_frame, fps),
    }


def _evidence_refs(item: Mapping[str, Any]) -> dict[str, object]:
    """事件图条目 → 建议载荷里可追溯的引用集合。"""
    refs: dict[str, object] = {}
    for key in (
        "event_ids",
        "entity_ids",
        "observation_ids",
        "segment_id",
        "language_id",
    ):
        if item.get(key):
            refs[key] = item[key]
    if item.get("attributes"):
        refs["attributes"] = item["attributes"]
    return refs


def _entry_rows(
    *,
    level: str,
    items: Iterable[Mapping[str, Any]],
    fps: float,
    model_id: str,
    model_version: str,
    prompt_version: str,
    generated_at: str | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in items:
        if str(item.get("review_state", "auto")) not in SUGGESTABLE_REVIEW_STATES:
            continue
        span = item.get("span")
        if not isinstance(span, Mapping):
            raise ValueError(f"{level} 条目缺 span")
        label = item.get("label") or item.get("text") or item.get("event_type")
        if not label:
            raise ValueError(f"{level} 条目缺 label/text/event_type")
        confidence = _number(item.get("confidence", 1.0), "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence 必须落在 [0,1]")
        row: dict[str, object] = {
            "label_key": f"{level}.{label}",
            "suggested_value": {
                "kind": "temporal_segment",
                "span": _span_to_value(span, fps),
                "label": label,
                "confidence": confidence,
                "evidence": _evidence_refs(item),
            },
            "model_id": model_id,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "confidence": confidence,
        }
        if generated_at:
            row["generated_at"] = generated_at
        rows.append(row)
    return rows


def convert_event_graph_to_suggestions(
    graph: Mapping[str, Any],
    *,
    model_id: str,
    model_version: str,
    prompt_version: str,
    generated_at: str | None = None,
) -> list[dict[str, object]]:
    """把一份事件图 JSON 转成 DDP ai-suggestions 载荷行（有序、去重由调用方负责）。

    拒绝非 auto 状态条目；拒绝缺 span/label 的条目（显式失败，不静默丢弃——
    与 DDP「未知/不支持必须显式失败」一致）。
    """
    schema = graph.get("schema")
    if schema != SCHEMA_VERSION:
        raise ValueError(f"不支持的事件图 schema: {schema}（期望 {SCHEMA_VERSION}）")
    fps = _number(graph.get("fps"), "fps")
    if fps <= 0:
        raise ValueError("fps 必须为正")
    if not graph.get("episode_id"):
        raise ValueError("事件图缺 episode_id")

    rows: list[dict[str, object]] = []
    rows.extend(
        _entry_rows(
            level="segment",
            items=graph.get("segments", ()) or (),
            fps=fps,
            model_id=model_id,
            model_version=model_version,
            prompt_version=prompt_version,
            generated_at=generated_at,
        )
    )
    rows.extend(
        _entry_rows(
            level="event",
            items=graph.get("events", ()) or (),
            fps=fps,
            model_id=model_id,
            model_version=model_version,
            prompt_version=prompt_version,
            generated_at=generated_at,
        )
    )
    rows.extend(
        _entry_rows(
            level="language",
            items=graph.get("language", ()) or (),
            fps=fps,
            model_id=model_id,
            model_version=model_version,
            prompt_version=prompt_version,
            generated_at=generated_at,
        )
    )
    return rows
