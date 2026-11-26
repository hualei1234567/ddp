"""MEM-DEC-002 偏好 JSON 约定值的提交写路径与导出兼容（V52-032）。

覆盖验收点：
- ``to_step_dpo_pair`` 产出的 JSON 约定值能通过 ``validate_record_labels``
  （即可以作为 ``JSON`` 标签值走既有 records 写入路径，不新增标签类型）；
- 非法约定值（缺必填字段 / winner 非 A/B/TIE / chosen==rejected / 未知字段）
  一律 ``LabelValueInvalidError``，提交侧映射 422；
- 通用 JSON 标签值不受偏好约定误伤；
- 落库形态（``AnnotationRecord`` 的 label value）能经
  ``preference_record_kwargs`` 被 ``ddp_data.llm_export.PreferenceRecord.from_raw``
  消费——annotation_records → 导出的兼容闭环。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation.enums import AnnotationLabelType, AnnotationLevel
from app.domain.annotation.errors import DomainValidationError, LabelValueInvalidError
from app.domain.annotation.prm_dpo import (
    looks_like_preference_json_value,
    preference_record_kwargs,
    to_step_dpo_pair,
)
from app.domain.annotation.prm_step import PrmAnnotation, StepMark, fork_from_step
from app.domain.annotation.schema import LabelDefinition
from app.domain.annotation.submission import AnnotationRecord, validate_record_labels
from ddp_data.llm_export import PreferenceRecord, Provenance, QcStatus, ReviewStatus

_LABEL_KEY = "preference"


def _preference_payload() -> dict[str, object]:
    """真实链路产物：fork 后 ``to_step_dpo_pair().to_payload()``。"""
    prm = PrmAnnotation(
        trajectory_id="traj-pref-1",
        step_actions=("查表", "抓取", "放置"),
        marks=(StepMark(0, "CORRECT"),),
        task_goal="把杯子从 A 移到 B",
    )
    forked = fork_from_step(prm, 1, "重抓杯子", "稳稳放置")
    return to_step_dpo_pair(prm, forked).to_payload()


def _definitions() -> tuple[LabelDefinition, ...]:
    return (
        LabelDefinition(
            label_key=_LABEL_KEY,
            label_type=AnnotationLabelType.JSON,
            required=False,
            level=AnnotationLevel.EPISODE,
        ),
    )


def _record(value: object) -> AnnotationRecord:
    return AnnotationRecord(
        annotation_id=uuid4(),
        annotation_lineage_id=uuid4(),
        annotation_version=1,
        project_id=uuid4(),
        submission_id=uuid4(),
        episode_id=uuid4(),
        episode_version=1,
        task_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        labels=[
            {
                "label_key": _LABEL_KEY,
                "value": value,
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )


def _provenance() -> Provenance:
    return Provenance(
        dataset_version_id="dv-1",
        sample_id="s-1",
        submission_id=str(uuid4()),
        review_status=ReviewStatus.APPROVED,
        qc_status=QcStatus.PASSED,
        schema_version="1.0.0",
    )


# ── 写路径校验 ────────────────────────────────────────────────────────────


def test_pair_payload_passes_label_validation() -> None:
    """fork 产出的约定值可直接作为 JSON 标签值通过提交校验。"""
    value = _preference_payload()
    validate_record_labels(_record(value), _definitions())


def test_generic_json_value_not_treated_as_preference() -> None:
    """不含偏好语义键的通用 JSON 值不受偏好约定约束。"""
    value = {"note": "普通 JSON 值", "tags": ["a", "b"]}
    assert not looks_like_preference_json_value(value)
    validate_record_labels(_record(value), _definitions())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda v: {k: x for k, x in v.items() if k != "winner"}, "缺少必填字段"),
        (lambda v: {k: x for k, x in v.items() if k != "source_trajectory_id"}, "缺少必填字段"),
        (lambda v: {k: x for k, x in v.items() if k != "fork_point_index"}, "缺少必填字段"),
        (lambda v: {**v, "winner": "chosen"}, "winner 只能是 A/B/TIE"),
        (lambda v: {**v, "winner": "C"}, "winner 只能是 A/B/TIE"),
        (lambda v: {**v, "rejected": v["chosen"]}, "不得相同"),
        (lambda v: {**v, "chosen": "  "}, "必须是非空字符串"),
        (lambda v: {**v, "fork_point_index": -1}, "fork_point_index 必须是非负整数"),
        (lambda v: {**v, "fork_point_index": "1"}, "fork_point_index 必须是非负整数"),
        (lambda v: {**v, "source_trajectory_id": ""}, "source_trajectory_id"),
        (lambda v: {**v, "winner2": "x"}, "未知字段"),
    ],
)
def test_invalid_preference_value_rejected(mutation, message: str) -> None:
    value = mutation(_preference_payload())
    with pytest.raises(LabelValueInvalidError, match=message):
        validate_record_labels(_record(value), _definitions())


def test_tie_value_accepted_by_write_path() -> None:
    """TIE 是合法呈现槽结论（导出侧按 PREFERENCE_TIE 剔除，不在写入口拒绝）。"""
    value = {**_preference_payload(), "winner": "TIE"}
    validate_record_labels(_record(value), _definitions())


# ── annotation_records → llm_export 兼容 ─────────────────────────────────


def test_record_value_feeds_preference_record_from_raw() -> None:
    """落库形态（record.labels 里的 JSON 值）能被 PreferenceRecord.from_raw 消费。"""
    record = _record(_preference_payload())
    value = record.labels[0].value
    kwargs = preference_record_kwargs(value, prompt="任务：把杯子从 A 移到 B")
    preference = PreferenceRecord.from_raw(_provenance(), **kwargs)
    assert preference.chosen == value["chosen"]
    assert preference.rejected == value["rejected"]
    assert preference.prompt == "任务：把杯子从 A 移到 B"
    assert preference.presented_order == ("A", "B")
    assert preference.is_tie is False
    # canonical 红线：A/B 呈现位置不影响 chosen/rejected 语义。
    assert preference.chosen != preference.rejected


def test_tie_record_value_feeds_from_raw_as_tie() -> None:
    value = {**_preference_payload(), "winner": "TIE"}
    record = _record(value)
    preference = PreferenceRecord.from_raw(
        _provenance(),
        **preference_record_kwargs(record.labels[0].value, prompt="任务：把杯子从 A 移到 B"),
    )
    assert preference.is_tie is True


def test_adapter_rejects_invalid_value() -> None:
    with pytest.raises(DomainValidationError, match="winner 只能是 A/B/TIE"):
        preference_record_kwargs(
            {**_preference_payload(), "winner": "chosen"},
            prompt="任务",
        )
    with pytest.raises(DomainValidationError, match="prompt"):
        preference_record_kwargs(_preference_payload(), prompt="  ")


def test_rationale_carried_into_export() -> None:
    value = {**_preference_payload(), "rationale": "重抓一次即成功，更稳"}
    preference = PreferenceRecord.from_raw(
        _provenance(),
        **preference_record_kwargs(_record(value).labels[0].value, prompt="任务"),
    )
    assert preference.rationale == "重抓一次即成功，更稳"
