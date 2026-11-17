from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from app import frames
from app.runs import RunRegistry
from app.tasks import execute_evaluation, execute_inference, execute_training

from tests.conftest import make_context
from tests.fakes import DATASET_VERSION_ID, RecordingCallbacks, seed_export


def _fake_extract(path, *, ffmpeg_path, ffprobe_path, count, size, timeout):
    # 真实抽帧在集成环境验证；单测只替换解码边界，保留训练/评测/推理全链路。
    # 用路径派生不同像素，避免所有视频特征完全相同导致优化器退化告警。
    seed = hashlib.sha256(str(path).encode()).digest()
    first = bytes(seed[index % len(seed)] for index in range(size * size))
    second = bytes((value + 37) % 256 for value in first)
    return frames.ExtractedFrames(
        frames=(first, second),
        sampling="test_stub",
        duration_seconds=1.0,
    )


def test_training_evaluation_and_inference_end_to_end(
    storage, settings, callbacks: RecordingCallbacks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frames, "extract_frames", _fake_extract)
    seed_export(storage)
    registry = RunRegistry()

    training_ctx = make_context(
        run_id="train-happy",
        kind="training",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_training(
        training_ctx,
        delivery_id=UUID("00000000-0000-0000-0000-000000000010"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={},
    )
    trained = registry.snapshot("train-happy")
    assert trained is not None
    assert trained["status"] == "SUCCEEDED", trained
    model_keys = [key for key in storage.objects if key.endswith("/model.joblib")]
    assert len(model_keys) == 1
    assert trained["metrics"]["frame_count"] == 6.0
    succeeded = callbacks.payloads("training:SUCCEEDED")
    assert len(succeeded) == 1
    assert set(succeeded[0]["model_ref"]) == {
        "model_id",
        "model_version",
        "registered_at",
    }
    assert succeeded[0]["model_ref"]["model_id"].startswith("local-adapter/train-happy")

    evaluation_id = UUID("00000000-0000-0000-0000-000000000020")
    eval_ctx = make_context(
        run_id="eval-happy",
        kind="evaluation",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_evaluation(
        eval_ctx,
        evaluation_id=evaluation_id,
        model_version_id=UUID("00000000-0000-0000-0000-000000000030"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={},
    )
    evaluated = registry.snapshot("eval-happy")
    assert evaluated is not None
    assert evaluated["status"] == "SUCCEEDED", evaluated
    assert 0.0 <= evaluated["metrics"]["accuracy"] <= 1.0
    reports = [key for key in storage.objects if key.endswith("/report.json")]
    assert len(reports) == 1
    completed = callbacks.payloads("evaluation.completed")
    assert len(completed) == 1
    assert completed[0]["evaluation_id"] == str(evaluation_id)
    assert completed[0]["report_ref"]["object_key"] == reports[0]

    task_id = UUID("00000000-0000-0000-0000-000000000040")
    infer_ctx = make_context(
        run_id="infer-happy",
        kind="inference",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_inference(
        infer_ctx,
        task_id=task_id,
        model_version_id=UUID("00000000-0000-0000-0000-000000000030"),
        service_id=None,
        dataset_version_id=UUID(DATASET_VERSION_ID),
        episode_ids=[UUID("00000000-0000-0000-0000-000000000099")],
        config={"episode_ids": ["ep-4"]},
    )
    # 上面的 UUID 不在导出里，会被过滤为空集，应明确失败而不是伪造结果。
    failed = registry.snapshot("infer-happy")
    assert failed is not None
    assert failed["status"] == "FAILED"
    assert failed["error_code"] in {
        "blocked_no_media_for_split",
        "blocked_no_media_episodes",
    }

    # 用真实存在的 episode 再跑一次推理。
    infer_ok_ctx = make_context(
        run_id="infer-ok",
        kind="inference",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_inference(
        infer_ok_ctx,
        task_id=task_id,
        model_version_id=UUID("00000000-0000-0000-0000-000000000030"),
        service_id=None,
        dataset_version_id=UUID(DATASET_VERSION_ID),
        episode_ids=[],
        config={"split": "test"},
    )
    inferred = registry.snapshot("infer-ok")
    assert inferred is not None
    assert inferred["status"] == "SUCCEEDED", inferred
    predictions = [key for key in storage.objects if key.endswith("/predictions.jsonl")]
    assert len(predictions) == 1
    body = storage.objects[predictions[0]].decode()
    assert body.count("\n") == 2
    assert '"predicted_action"' in body
    completed = callbacks.payloads("inference.completed")
    assert len(completed) == 1
    assert completed[0]["result_ref"]["row_count"] == 2


def test_evaluation_without_media_fails_with_error_callback(
    storage, settings, callbacks: RecordingCallbacks
) -> None:
    seed_export(storage, with_media=False, with_labels=False)
    registry = RunRegistry()
    ctx = make_context(
        run_id="eval-blocked",
        kind="evaluation",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_evaluation(
        ctx,
        evaluation_id=UUID("00000000-0000-0000-0000-000000000050"),
        model_version_id=UUID("00000000-0000-0000-0000-000000000051"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={"project_id": "1bca073d-c9af-4601-9b0f-7888d0747db0"},
    )
    snapshot = registry.snapshot("eval-blocked")
    assert snapshot is not None
    assert snapshot["status"] == "FAILED"
    assert snapshot["error_code"] == "blocked_media_manifest_missing"
    failed = callbacks.payloads("evaluation.failed")
    assert len(failed) == 1
    assert failed[0]["project_id"] == "1bca073d-c9af-4601-9b0f-7888d0747db0"
    assert failed[0]["error_code"] == "blocked_media_manifest_missing"


def test_training_reads_episodes_jsonl_labels(
    storage, settings, callbacks: RecordingCallbacks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frames, "extract_frames", _fake_extract)
    prefix = seed_export(storage, with_media=True, with_labels=False)
    import json

    rows = [
        {"episode_id": "ep-1", "split": "TRAIN", "action": "PICK", "success": True},
        {"episode_id": "ep-2", "split": "TRAIN", "action": "PLACE", "success": False},
        {"episode_id": "ep-3", "split": "TRAIN", "action": "PICK", "success": True},
        {"episode_id": "ep-4", "split": "TEST", "action": "PLACE"},
    ]
    storage.put_bytes(
        f"{prefix}data/episodes.jsonl",
        ("\n".join(json.dumps(row) for row in rows) + "\n").encode(),
        content_type="application/x-ndjson",
    )
    registry = RunRegistry()
    ctx = make_context(
        run_id="train-jsonl",
        kind="training",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    execute_training(
        ctx,
        delivery_id=UUID("00000000-0000-0000-0000-000000000060"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={},
    )
    snapshot = registry.snapshot("train-jsonl")
    assert snapshot is not None
    assert snapshot["status"] == "SUCCEEDED", snapshot
    assert snapshot["artifacts"]["label_source"] == "jsonl"
    assert snapshot["artifacts"]["classes"] == ["pick", "place"]
