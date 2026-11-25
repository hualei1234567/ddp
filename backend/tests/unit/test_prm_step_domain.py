"""PRM 步级标注域单测（V52-020）。

**判定标准**：四分类 reward 映射与 ``ddp_data.llm_export.prm`` **数值完全相等**
（防双源漂移的红线测试）；index 唯一且可部分标注；fork 边界（0 / 末步 / 越界）
行为明确；Step-DPO 对 chosen/rejected 语义与 ``PreferenceRecord.from_raw`` 兼容。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.prm_dpo import (
    StepDpoPair,
    to_step_dpo_pair,
    validate_preference_payload,
)
from app.domain.annotation.prm_step import (
    REWARD_BY_LABEL,
    ForkedTrajectory,
    PrmAnnotation,
    StepLabelKind,
    StepMark,
    fork_from_step,
)
from ddp_data.llm_export import Provenance, QcStatus, ReviewStatus
from ddp_data.llm_export.prm import REWARD_BY_LABEL as EXPORT_REWARD_BY_LABEL
from ddp_data.llm_export.prm import StepLabel as ExportStepLabel

# ── 红线：四分类 reward 映射与 llm_export 完全一致（防双源漂移） ──────────


def test_reward_mapping_matches_llm_export_exactly() -> None:
    """键集合与数值都必须相等：任一侧单独漂移 CI 直接红。"""
    assert REWARD_BY_LABEL == EXPORT_REWARD_BY_LABEL
    assert set(REWARD_BY_LABEL) == {label.value for label in StepLabelKind}
    assert set(REWARD_BY_LABEL) == {label.value for label in ExportStepLabel}


def test_four_label_kinds_with_blueprint_rewards() -> None:
    assert StepLabelKind.CORRECT.reward == 1.0
    assert StepLabelKind.REDUNDANT.reward == -0.5
    assert StepLabelKind.HALLUCINATED_TOOL.reward == -1.0
    assert StepLabelKind.FATAL.reward == -2.0


def test_unknown_label_kind_rejected() -> None:
    with pytest.raises(DomainValidationError, match="非法步级标签"):
        StepMark(0, "PERFECT")


# ── StepMark 基本校验 ────────────────────────────────────────────


def test_step_mark_negative_index_rejected() -> None:
    with pytest.raises(DomainValidationError, match="不能为负"):
        StepMark(-1, StepLabelKind.CORRECT)


def test_step_mark_optional_fields_roundtrip() -> None:
    marked_at = datetime(2026, 11, 25, tzinfo=UTC)
    mark = StepMark(
        2, StepLabelKind.FATAL, comment="抓空了", annotator="alice", marked_at=marked_at
    )
    payload = mark.to_payload()
    assert payload["label"] == "FATAL"
    assert payload["marked_at"] == marked_at.isoformat()
    rebuilt = StepMark(**payload)  # type: ignore[arg-type]
    assert rebuilt == mark


# ── PrmAnnotation：index 唯一、可部分标注 ─────────────────────────


def _prm(*marks: StepMark) -> PrmAnnotation:
    return PrmAnnotation(
        "traj-1",
        ("查表", "抓取", "放置"),
        marks=marks,
        task_goal="移动杯子",
    )


def test_partial_annotation_allowed_and_unmarked_is_not_correct() -> None:
    """可部分标注：只标一步也合法；未标注步不产生任何结论。"""
    prm = _prm(StepMark(0, StepLabelKind.CORRECT))
    assert prm.labeled_step_count == 1
    assert prm.mark_at(1) is None
    assert prm.mark_at(2) is None
    assert prm.total_reward() == 1.0


def test_duplicate_step_index_rejected() -> None:
    with pytest.raises(DomainValidationError, match="重复标注"):
        _prm(StepMark(0, StepLabelKind.CORRECT), StepMark(0, StepLabelKind.FATAL))


def test_out_of_range_mark_rejected() -> None:
    with pytest.raises(DomainValidationError, match="越界"):
        _prm(StepMark(3, StepLabelKind.CORRECT))


def test_empty_trajectory_rejected() -> None:
    with pytest.raises(DomainValidationError, match="至少需要 1 个步骤"):
        PrmAnnotation("traj-1", ())


def test_with_mark_overwrites_existing_index() -> None:
    prm = _prm(StepMark(1, StepLabelKind.REDUNDANT))
    updated = prm.with_mark(StepMark(1, StepLabelKind.CORRECT))
    assert updated.mark_at(1) is not None
    assert updated.mark_at(1).label is StepLabelKind.CORRECT  # type: ignore[union-attr]
    assert updated.labeled_step_count == 1


def test_payload_roundtrip_preserves_marks() -> None:
    prm = _prm(StepMark(0, StepLabelKind.CORRECT), StepMark(2, StepLabelKind.FATAL, comment="x"))
    rebuilt = PrmAnnotation.from_payload(prm.to_payload())
    assert rebuilt == prm


def test_payload_unknown_field_rejected() -> None:
    with pytest.raises(DomainValidationError, match="未知字段"):
        PrmAnnotation.from_payload(
            {"trajectory_id": "t", "step_actions": ["a"], "marks": [], "extra": 1}
        )


# ── fork_from_step 边界 ──────────────────────────────────────────


def test_fork_at_zero_replaces_everything() -> None:
    prm = _prm(StepMark(0, StepLabelKind.CORRECT))
    forked = fork_from_step(prm, 0, "直接放置")
    assert forked.step_actions == ("直接放置",)
    assert forked.fork_point_index == 0
    assert forked.marks == ()  # 整条重写：原标注不保留
    assert forked.source_trajectory_id == "traj-1"


def test_fork_at_last_step_keeps_prefix_marks() -> None:
    prm = _prm(StepMark(0, StepLabelKind.CORRECT), StepMark(1, StepLabelKind.REDUNDANT))
    forked = fork_from_step(prm, 2, "轻拿轻放", "复位")
    assert forked.step_actions == ("查表", "抓取", "轻拿轻放", "复位")
    assert [m.step_index for m in forked.marks] == [0, 1]
    assert forked.suffix_actions() == ("轻拿轻放", "复位")


def test_fork_out_of_range_rejected() -> None:
    prm = _prm()
    with pytest.raises(DomainValidationError, match="越界"):
        fork_from_step(prm, 3, "新动作")
    with pytest.raises(DomainValidationError, match="越界"):
        fork_from_step(prm, -1, "新动作")


def test_fork_empty_rewrite_rejected() -> None:
    with pytest.raises(DomainValidationError, match="不能为空"):
        fork_from_step(_prm(), 1)


def test_fork_rewrite_steps_are_unmarked() -> None:
    """重写步骤不携带先验结论（未标注 ≠ 正确）。"""
    forked = fork_from_step(_prm(StepMark(0, StepLabelKind.CORRECT)), 1, "重抓", "放置")
    assert forked.mark_at(1) is None
    assert forked.mark_at(2) is None


def test_forked_trajectory_materializes_with_lineage() -> None:
    """分叉轨迹物化为独立 PrmAnnotation 时记录血缘且可再持久化。"""
    prm = _prm(StepMark(0, StepLabelKind.CORRECT))
    forked = fork_from_step(prm, 1, "重抓", "放置")
    materialized = forked.to_prm_annotation("traj-1#fork-1")
    assert materialized.trajectory_id == "traj-1#fork-1"
    assert materialized.fork_lineage is not None
    assert materialized.fork_lineage["source_trajectory_id"] == "traj-1"
    assert materialized.fork_lineage["fork_point_index"] == 1
    rebuilt = PrmAnnotation.from_payload(materialized.to_payload())
    assert rebuilt == materialized


# ── Step-DPO 对构造 ──────────────────────────────────────────────


def _pair_fixture() -> tuple[PrmAnnotation, ForkedTrajectory]:
    prm = _prm(StepMark(0, StepLabelKind.CORRECT), StepMark(1, StepLabelKind.HALLUCINATED_TOOL))
    forked = fork_from_step(prm, 1, "重抓杯子", "稳稳放置")
    return prm, forked


def test_step_dpo_pair_semantics() -> None:
    prm, forked = _pair_fixture()
    pair = to_step_dpo_pair(prm, forked)
    assert pair.fork_point_index == 1
    assert pair.chosen == "[1] 重抓杯子\n[2] 稳稳放置"
    assert pair.rejected == "[1] 抓取\n[2] 放置"
    assert "[0] 查表" in pair.prompt
    assert pair.chosen != pair.rejected


def test_step_dpo_pair_shares_prefix_in_prompt_only() -> None:
    """分叉点前共享前缀只进 prompt，chosen/rejected 只含分叉点后序列。"""
    prm, forked = _pair_fixture()
    pair = to_step_dpo_pair(prm, forked)
    assert "抓取" not in pair.chosen
    assert "重抓杯子" not in pair.rejected


def test_identical_suffix_rejected() -> None:
    """重写序列与原序列完全相同 → 无差异，不成对。"""
    prm, forked = _pair_fixture()
    same = fork_from_step(prm, 1, "抓取", "放置")
    with pytest.raises(DomainValidationError, match="不得相同"):
        to_step_dpo_pair(prm, same)


def test_foreign_forked_trajectory_rejected() -> None:
    prm, forked = _pair_fixture()
    forged = ForkedTrajectory(
        source_trajectory_id="other",
        fork_point_index=forked.fork_point_index,
        step_actions=forked.step_actions,
    )
    with pytest.raises(DomainValidationError, match="同一 trajectory_id"):
        to_step_dpo_pair(prm, forged)


def test_pair_criteria_scores_carry_rewards() -> None:
    prm, forked = _pair_fixture()
    pair = to_step_dpo_pair(prm, forked)
    assert pair.criteria_scores["original_total_reward"] == 0.0
    assert pair.criteria_scores["shared_prefix_reward"] == 1.0


# ── PreferenceRecord 兼容（MEM-DEC-002：JSON 约定值，不加 LabelType） ──


def test_pair_feeds_preference_record_from_raw() -> None:
    """to_preference_record_kwargs 必须 能直接喂 PreferenceRecord.from_raw。"""
    prm, forked = _pair_fixture()
    pair = to_step_dpo_pair(prm, forked)
    provenance = Provenance(
        dataset_version_id="dv-1",
        sample_id="s-1",
        submission_id=str(uuid4()),
        review_status=ReviewStatus.APPROVED,
        qc_status=QcStatus.PASSED,
        schema_version="1.0.0",
    )
    from ddp_data.llm_export import PreferenceRecord

    record = PreferenceRecord.from_raw(provenance, **pair.to_preference_record_kwargs())
    assert isinstance(record.chosen, str)
    assert record.chosen == pair.chosen
    assert record.rejected == pair.rejected
    assert record.prompt == pair.prompt
    assert not record.is_tie


def test_preference_json_payload_shape() -> None:
    """JSON 约定值形态：winner 用 chosen 语义键并可通过校验器。"""
    prm, forked = _pair_fixture()
    pair = to_step_dpo_pair(prm, forked)
    payload = pair.to_payload()
    assert payload["winner"] == "chosen"
    assert payload["fork_point_index"] == 1
    validate_preference_payload(payload)
    with pytest.raises(DomainValidationError, match="未知字段"):
        validate_preference_payload({**payload, "winner2": "x"})


def test_empty_pair_text_rejected() -> None:
    with pytest.raises(DomainValidationError, match="不能为空"):
        StepDpoPair(
            source_trajectory_id="t",
            fork_point_index=0,
            prompt="p",
            chosen=" ",
            rejected="r",
        )


def test_invalid_criteria_score_rejected() -> None:
    with pytest.raises(DomainValidationError, match="数值"):
        StepDpoPair(
            source_trajectory_id="t",
            fork_point_index=0,
            prompt="p",
            chosen="c",
            rejected="r",
            criteria_scores={"a": "1"},
        )
