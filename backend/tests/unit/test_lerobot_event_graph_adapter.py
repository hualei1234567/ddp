"""事件图 → AI 建议适配器测试（V31-046 切片 C）。"""

from __future__ import annotations

import json

import pytest
from ddp_data.lerobot.event_graph_adapter import (
    SCHEMA_VERSION,
    convert_event_graph_to_suggestions,
)

MODEL = {
    "model_id": "interaction-auto-labeler-v3",
    "model_version": "S1+S2+S3-beta",
    "prompt_version": "template/1.0.0",
}


def sample_graph() -> dict[str, object]:
    return {
        "schema": SCHEMA_VERSION,
        "dataset_id": "ddp-demo",
        "dataset_version": "1",
        "episode_id": "ep-0001",
        "frame_count": 300,
        "fps": 25.0,
        "segments": [
            {
                "segment_id": "seg-task-1",
                "level": "task",
                "label": "pick_and_place",
                "span": {"start_frame": 0, "end_frame": 299},
                "confidence": 0.97,
                "review_state": "auto",
            },
            {
                "segment_id": "seg-event-1",
                "level": "event",
                "label": "grasp",
                "span": {"start_frame": 40, "end_frame": 90},
                "confidence": 0.82,
                # 已人工决定：必须跳过
                "review_state": "human_confirmed",
            },
        ],
        "events": [
            {
                "event_id": "ev-1",
                "event_type": "contact",
                "span": {"start_frame": 40, "end_frame": 45},
                "confidence": 0.75,
                "target_entity_id": "ent-1",
                "observation_ids": ["obs-1", "obs-2"],
                "review_state": "auto",
            }
        ],
        "language": [
            {
                "language_id": "lang-1",
                "level": "task",
                "text": "pick up the red block and place it on the tray",
                "span": {"start_frame": 0, "end_frame": 299},
                "confidence": 0.9,
                "review_state": "auto",
            }
        ],
    }


def test_convert_produces_namespaced_label_keys() -> None:
    rows = convert_event_graph_to_suggestions(sample_graph(), **MODEL)
    keys = [row["label_key"] for row in rows]
    assert "segment.pick_and_place" in keys
    assert "event.contact" in keys
    assert "language.pick up the red block and place it on the tray" in keys
    # 人工已决定的 seg-event-1 必须被跳过
    assert all("grasp" not in k for k in keys)


def test_span_frames_convert_to_ns_with_fps() -> None:
    rows = convert_event_graph_to_suggestions(sample_graph(), **MODEL)
    contact = next(r for r in rows if r["label_key"] == "event.contact")
    value = contact["suggested_value"]
    assert value["span"]["start_time_ns"] == int(40 / 25 * 1e9)  # 1_600_000_000
    assert value["span"]["end_time_ns"] == int(45 / 25 * 1e9)
    assert value["span"]["start_frame"] == 40
    # 证据引用原样保留
    assert value["evidence"]["observation_ids"] == ["obs-1", "obs-2"]
    assert "entity_ids" not in value["evidence"]  # 未引用就不出现在证据里


def test_model_identity_required_in_every_row() -> None:
    rows = convert_event_graph_to_suggestions(sample_graph(), **MODEL)
    assert rows, "应产出建议行"
    for row in rows:
        assert row["model_id"] == MODEL["model_id"]
        assert row["model_version"] == MODEL["model_version"]
        assert row["prompt_version"] == MODEL["prompt_version"]
        assert 0.0 <= row["confidence"] <= 1.0


def test_wrong_schema_rejected() -> None:
    graph = sample_graph()
    graph["schema"] = "embodied_event_graph_v2"
    with pytest.raises(ValueError, match="schema"):
        convert_event_graph_to_suggestions(graph, **MODEL)


def test_bad_confidence_rejected() -> None:
    graph = sample_graph()
    graph["segments"][0]["confidence"] = 1.5  # type: ignore[index]
    with pytest.raises(ValueError, match="confidence"):
        convert_event_graph_to_suggestions(graph, **MODEL)


def test_output_is_serializable_payload() -> None:
    """输出必须能直接作为 POST ai-suggestions 的 JSON 载荷。"""
    rows = convert_event_graph_to_suggestions(
        sample_graph(), generated_at="2026-11-23T02:00:00+00:00", **MODEL
    )
    body = json.loads(json.dumps({"suggestions": rows}, ensure_ascii=False))
    assert len(body["suggestions"]) == len(rows)
    assert body["suggestions"][0]["generated_at"] == "2026-11-23T02:00:00+00:00"
