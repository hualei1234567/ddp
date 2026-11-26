"""LLM 训练导出 API 集成测试（V52-037）。

覆盖验收点：
- SFT / DPO / PRM 三格式各一条成功路径：``record_count`` 与产物 ``sha256``
  存在，且响应**不含**训练文件内容；
- 未知格式 422（请求模型拒绝）；
- 记录缺该格式必填字段 422（应用层映射，不允许 KeyError 变成 500）；
- 未过门禁的记录被剔除并计入 ``excluded_counts``（不伪造成功）；
- viewer 无写权限 403；只读用户可读格式列表。
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import url
from tests.api.conftest import ApiIds, make_client

_PATH = "/llm-exports"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _provenance(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset_version_id": "dsv-llm-1",
        "sample_id": "sample-llm-1",
        "submission_id": "submission-llm-1",
        "schema_version": "ddp.annotation/1.0.0",
        "review_status": "APPROVED",
        "qc_status": "PASSED",
        "annotator_refs": ["sha256:" + "a" * 64],
    }
    payload.update(overrides)
    return payload


def _sft_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "provenance": _provenance(),
        "messages": [
            {"role": "system", "content": "你是家用机器人"},
            {"role": "user", "content": "把杯子放到桌上"},
            {"role": "assistant", "content": "好的，已把杯子放到桌上"},
        ],
    }
    record.update(overrides)
    return record


def _dpo_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "provenance": _provenance(),
        "prompt": "把杯子放到桌上",
        "response_a": "好的，已放到桌上",
        "response_b": "把杯子扔了",
        "winner": "A",
        "rationale": "A 完成了指令",
        "criteria_scores": {"task_success": 1.0, "safety": 0.5},
        "confidence": 0.9,
        "presented_order": ["A", "B"],
    }
    record.update(overrides)
    return record


def _prm_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "provenance": _provenance(),
        "trajectory_id": "traj-llm-1",
        "task_goal": "把杯子从 A 移到 B",
        "steps": [
            {"index": 0, "action": "查表", "label": "CORRECT"},
            {
                "index": 1,
                "action": "调用抓取工具但未命中",
                "label": "HALLUCINATED_TOOL",
                "tool": "grasp",
            },
        ],
        "final_answer_correct": True,
    }
    record.update(overrides)
    return record


def _assert_sha256_files(body: dict[str, object], expected: set[str]) -> None:
    files = body["files"]
    assert isinstance(files, dict)
    assert set(files) == expected
    for digest in files.values():
        assert _SHA256_HEX.match(digest["sha256"])
        assert isinstance(digest["bytes"], int) and digest["bytes"] > 0
    # 摘要端点不回传训练文件内容。
    assert all("content" not in digest for digest in files.values())


def test_export_sft_success(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "SFT", "records": [_sft_record()]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "SFT_MESSAGES_JSONL"
    assert body["format_version"] == "1.0.0"
    assert body["generator_version"] == "ddp.llm-export/1.0.0"
    assert body["record_count"] == 1
    assert body["excluded_counts"] == {}
    _assert_sha256_files(body, {"sft_messages.jsonl", "provenance.jsonl"})


def test_export_dpo_success(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "DPO", "records": [_dpo_record()]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "PREFERENCE_DPO_JSONL"
    assert body["record_count"] == 1
    assert body["excluded_counts"] == {}
    _assert_sha256_files(body, {"preference_dpo.jsonl", "provenance.jsonl"})


def test_export_prm_success(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "PRM", "records": [_prm_record()]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "PRM_STEP_REWARD_JSONL"
    assert body["record_count"] == 1
    assert body["excluded_counts"] == {}
    _assert_sha256_files(body, {"prm_step_reward.jsonl", "provenance.jsonl"})


def test_unknown_format_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "GRPO", "records": [_sft_record()]},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_missing_format_specific_field_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """DPO 记录缺 winner：应用层按格式校验返回 422，不把 KeyError 变成 500。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    record = _dpo_record()
    record.pop("winner")

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "DPO", "records": [record]},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_ineligible_records_counted_as_excluded(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """未审核记录由库的门禁剔除：SFT 导出 0 行、剔除统计可解释，不伪造成功。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    record = _sft_record(provenance=_provenance(review_status="PENDING"))

    response = editor.post(
        url(ids.project_a, _PATH),
        json={"format": "SFT", "records": [record]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record_count"] == 0
    assert body["excluded_counts"] == {"REVIEW_NOT_APPROVED": 1}


def test_viewer_cannot_create_export(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    response = viewer.post(
        url(ids.project_a, _PATH),
        json={"format": "SFT", "records": [_sft_record()]},
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_formats_readable_by_viewer(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    response = viewer.get(url(ids.project_a, f"{_PATH}/formats"))

    assert response.status_code == 200, response.text
    formats = response.json()["formats"]
    assert [item["format"] for item in formats] == ["SFT", "DPO", "PRM"]
    assert all(item["format_version"] == "1.0.0" for item in formats)
