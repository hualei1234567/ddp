"""PRM Step-Reward 导出测试（V52-017）。

**判定标准**：四类标签 reward 映射正确；index 连续性强制；未标注步骤
不进训练文件；资格门禁整条剔除；roundtrip 校验 reward/label 一致。
"""

from __future__ import annotations

import pytest

from ddp_data.llm_export import (
    LlmExportError,
    Provenance,
    QcStatus,
    ReviewStatus,
)
from ddp_data.llm_export.prm import (
    PRM_FORMAT,
    PrmExportError,
    REWARD_BY_LABEL,
    StepLabel,
    TrajectoryRecord,
    TrajectoryStep,
    export_prm,
    parse_prm_bundle,
)


def _prov(sample: str = "tr-1", **kw: object) -> Provenance:
    return Provenance(
        dataset_version_id="dv-2",
        sample_id=sample,
        submission_id=f"sub-{sample}",
        review_status=ReviewStatus.APPROVED,
        qc_status=QcStatus.PASSED,
        schema_version="agent-trace/v1",
        **kw,  # type: ignore[arg-type]
    )


def _steps(labels: list[str | None]) -> list[dict[str, object]]:
    return [
        {
            "index": i,
            "action": f"step-{i}",
            "label": lbl,
            "tool": "bash" if lbl == "HALLUCINATED_TOOL" else None,
        }
        for i, lbl in enumerate(labels)
    ]


def _traj(
    sample: str = "tr-1", labels: list[str | None] | None = None, **kw: object
) -> TrajectoryRecord:
    labels = (
        labels if labels is not None else ["CORRECT", "CORRECT", "REDUNDANT", "CORRECT"]
    )
    prov_kw = {k: kw.pop(k) for k in list(kw) if k in ("review", "qc")}
    return TrajectoryRecord.from_raw(
        _prov(sample, **prov_kw),  # type: ignore[arg-type]
        trajectory_id=f"traj-{sample}",
        task_goal="修复 failing test",
        steps=_steps(labels),
        **kw,  # type: ignore[arg-type]
    )


def test_reward_mapping_matches_blueprint_labels() -> None:
    assert REWARD_BY_LABEL == {
        "CORRECT": 1.0,
        "REDUNDANT": -0.5,
        "HALLUCINATED_TOOL": -1.0,
        "FATAL": -2.0,
    }
    step = TrajectoryStep(index=0, action="x", label=StepLabel.FATAL)
    assert step.reward == -2.0
    unlabeled = TrajectoryStep(index=1, action="y")
    with pytest.raises(PrmExportError, match="未标注"):
        _ = unlabeled.reward


def test_index_must_be_contiguous() -> None:
    with pytest.raises(PrmExportError, match="连续"):
        TrajectoryRecord.from_raw(
            _prov(),
            trajectory_id="t",
            task_goal="g",
            steps=[{"index": 0, "action": "a"}, {"index": 2, "action": "b"}],
        )
    with pytest.raises(PrmExportError, match="至少需要 1 个步骤"):
        TrajectoryRecord.from_raw(_prov(), trajectory_id="t", task_goal="g", steps=[])


def test_unlabeled_steps_excluded_from_training_file() -> None:
    record = _traj(labels=["CORRECT", None, "FATAL", "CORRECT"])
    bundle = export_prm([record])
    docs = parse_prm_bundle(bundle)
    assert len(docs[0]["steps"]) == 3
    assert [s["index"] for s in docs[0]["steps"]] == [0, 2, 3]
    assert docs[0]["steps"][1]["label"] == "FATAL"


def test_manifest_carries_reward_mapping_and_totals() -> None:
    bundle = export_prm(
        [
            _traj("a"),
            _traj("b", labels=["FATAL", "HALLUCINATED_TOOL", "CORRECT", "CORRECT"]),
        ]
    )
    assert bundle.manifest["format"] == PRM_FORMAT
    assert bundle.manifest["reward_mapping"] == REWARD_BY_LABEL
    assert bundle.manifest["label_counts_total"]["CORRECT"] == 5
    assert bundle.manifest["label_counts_total"]["FATAL"] == 1


def test_gate_excludes_unreviewed_whole_trajectory() -> None:
    records = [_traj("ok"), _traj("bad", review=ReviewStatus.REJECTED)]
    bundle = export_prm(records)
    docs = parse_prm_bundle(bundle)
    assert len(docs) == 1
    assert bundle.manifest["excluded_counts"] == {"REVIEW_NOT_APPROVED": 1}


def test_all_excluded_raises() -> None:
    with pytest.raises(PrmExportError, match="无可导出"):
        export_prm([_traj("x", qc=QcStatus.FAILED)])


def test_duplicate_trajectory_id_rejected() -> None:
    with pytest.raises(PrmExportError, match="重复"):
        export_prm([_traj("dup"), _traj("dup")])


def test_export_is_byte_identical() -> None:
    b1 = export_prm([_traj("a"), _traj("b")])
    b2 = export_prm([_traj("a"), _traj("b")])
    assert b1.files["prm_step_reward.jsonl"] == b2.files["prm_step_reward.jsonl"]
    assert (
        b1.manifest["files"]["prm_step_reward.jsonl"]
        == b2.manifest["files"]["prm_step_reward.jsonl"]
    )


def test_roundtrip_detects_reward_label_mismatch() -> None:
    bundle = export_prm([_traj("a")])
    parse_prm_bundle(bundle)  # 正常通过
    # 篡改 reward 与 label 不一致 → 回读必须拒绝
    tampered_line = (
        bundle.files["prm_step_reward.jsonl"]
        .decode("utf-8")
        .replace('"reward":1.0', '"reward":0.5', 1)
    )
    bundle.files["prm_step_reward.jsonl"] = tampered_line.encode("utf-8")
    with pytest.raises(PrmExportError, match="reward 与 label 不符"):
        parse_prm_bundle(bundle)


def test_provenance_sidecar_has_step_stats() -> None:
    import json

    bundle = export_prm(
        [_traj("a"), _traj("b", labels=["CORRECT", "CORRECT", None, None])]
    )
    lines = bundle.files["provenance.jsonl"].decode("utf-8").splitlines()
    assert len(lines) == 2
    doc = json.loads(lines[0])
    assert doc["labeled_steps"] == 4
    assert doc["total_reward"] == pytest.approx(2.5)  # 1+1-0.5+1
    doc2 = json.loads(lines[1])
    assert doc2["labeled_steps"] == 2
    assert doc2["total_reward"] == 2.0


def test_export_requires_records() -> None:
    with pytest.raises(PrmExportError, match="至少需要 1 条"):
        export_prm([])


def test_invalid_step_payloads_rejected() -> None:
    with pytest.raises((PrmExportError, LlmExportError)):
        TrajectoryRecord.from_raw(
            _prov(), trajectory_id="", task_goal="g", steps=_steps(["CORRECT"])
        )
    with pytest.raises(PrmExportError):
        TrajectoryStep(index=-1, action="x", label=StepLabel.CORRECT)
