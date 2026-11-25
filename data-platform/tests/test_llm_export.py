"""LLM 训练导出库测试（V52-015/016）。

**判定标准**：门禁按红线 1 硬剔除未审核记录；输出字节级确定；
偏好 canonical 语义与呈现位置无关；round-trip 自洽。

第三方真实验证（datasets/trl 加载）在 docs/testing/evidence/ 另存脚本。
"""

from __future__ import annotations

import json

import pytest

from ddp_data.llm_export import (
    LlmExportError,
    PreferenceRecord,
    Provenance,
    QcStatus,
    ReviewStatus,
    SftConversationRecord,
    assert_single_kind,
    partition_by_eligibility,
)
from ddp_data.llm_export.readers import (
    assert_no_pii,
    check_provenance_alignment,
    parse_dpo_bundle,
    parse_sft_bundle,
    verify_manifest,
)
from ddp_data.llm_export.writers import (
    LlmExportWriterError,
    export_dpo,
    export_sft,
)


def _prov(
    *,
    review: ReviewStatus = ReviewStatus.APPROVED,
    qc: QcStatus = QcStatus.PASSED,
    sample: str = "s-1",
) -> Provenance:
    return Provenance(
        dataset_version_id="dv-1",
        sample_id=sample,
        submission_id=f"sub-{sample}",
        review_status=review,
        qc_status=qc,
        schema_version="preference-pair/v1",
        guideline_version="gl-3",
        annotator_refs=("anon-a", "anon-b"),
    )


def _sft(sample: str = "s-1", **prov_kw: object) -> SftConversationRecord:
    return SftConversationRecord.from_raw(
        _prov(sample=sample, **prov_kw),  # type: ignore[arg-type]
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "总结这段话"},
            {"role": "assistant", "content": "好的，以下是总结。"},
        ],
    )


def _pref(sample: str = "p-1", **kw: object) -> PreferenceRecord:
    defaults: dict[str, object] = {
        "winner": "A",
        "presented_order": ("A", "B"),
        "rationale": "B 更简洁",
        "confidence": 0.9,
    }
    defaults.update(kw)
    return PreferenceRecord.from_raw(
        _prov(sample=sample),  # type: ignore[arg-type]
        prompt="解释递归",
        response_a="递归是函数调用自身…",
        response_b="递归即自调用…",
        **defaults,  # type: ignore[arg-type]
    )


# ── 领域模型 ──────────────────────────────────────────────


def test_chat_message_rejects_bad_role_and_empty() -> None:
    with pytest.raises(LlmExportError, match="非法消息角色"):
        SftConversationRecord.from_raw(_prov(), [{"role": "human", "content": "x"}])
    with pytest.raises(LlmExportError, match="不能为空"):
        SftConversationRecord.from_raw(_prov(), [{"role": "user", "content": " "}])
    with pytest.raises(LlmExportError, match="不能为空"):
        SftConversationRecord.from_raw(_prov(), [])


def test_provenance_requires_core_fields() -> None:
    with pytest.raises(LlmExportError, match="sample_id"):
        Provenance(
            dataset_version_id="dv",
            sample_id=" ",
            submission_id="s",
            review_status=ReviewStatus.APPROVED,
            qc_status=QcStatus.PASSED,
            schema_version="x",
        )


def test_preference_canonical_mapping_independent_of_presentation() -> None:
    """同一语义偏好，A/B 呈现顺序翻转后导出的 chosen/rejected 必须一致。"""
    a = _pref(winner="A", presented_order=("A", "B"))
    b = _pref(winner="B", presented_order=("B", "A"))
    assert a.chosen == b.chosen == "递归是函数调用自身…"
    assert a.rejected == b.rejected == "递归即自调用…"

    tie = _pref(winner="TIE")
    assert tie.chosen == "递归是函数调用自身…"  # TIE 原样保留，由门禁剔除


def test_preference_rejects_invalid_winner_and_confidence() -> None:
    with pytest.raises(LlmExportError, match="winner"):
        _pref(winner="C")
    with pytest.raises(LlmExportError, match="confidence"):
        _pref(confidence=1.5)
    with pytest.raises(LlmExportError, match="presented_order"):
        _pref(presented_order=("A", "C"))


# ── 门禁 ──────────────────────────────────────────────────


def test_gate_excludes_unreviewed_and_failed_qc_with_reasons() -> None:
    records = [
        _sft("ok-1"),
        _sft("bad-review", review=ReviewStatus.REJECTED),
        _sft("pending-review", review=ReviewStatus.PENDING),
        _sft("qc-fail", qc=QcStatus.FAILED),
        _sft("qc-pending", qc=QcStatus.PENDING),
    ]
    report = partition_by_eligibility(records)
    assert [p.sample_id for p in report.eligible] == ["ok-1"]
    assert report.excluded_counts == {
        "REVIEW_NOT_APPROVED": 2,
        "QC_FAILED": 1,
        "QC_PENDING": 1,
    }


def test_tie_preferences_are_excluded_by_gate() -> None:
    records = [_pref("keep", winner="A"), _pref("drop", winner="TIE")]
    report = partition_by_eligibility(records)
    assert [p.sample_id for p in report.eligible] == ["keep"]
    assert report.excluded_counts == {"PREFERENCE_TIE": 1}


# ── SFT writer ───────────────────────────────────────────


def test_sft_export_clean_training_file_with_sidecar() -> None:
    bundle = export_sft([_sft("a"), _sft("b")])
    docs = parse_sft_bundle(bundle)
    assert len(docs) == 2
    assert set(docs[0]) == {"messages"}  # 训练文件纯净
    assert docs[0]["messages"][0]["role"] == "system"
    check_provenance_alignment(bundle, rows=2)
    verify_manifest(bundle)


def test_sft_export_drops_ineligible_and_counts_them() -> None:
    bundle = export_sft([_sft("good"), _sft("bad", review=ReviewStatus.REJECTED)])
    docs = parse_sft_bundle(bundle)
    assert len(docs) == 1
    assert bundle.manifest["excluded_counts"] == {"REVIEW_NOT_APPROVED": 1}
    assert bundle.manifest["record_count"] == 1


def test_sft_export_is_byte_identical_for_same_input() -> None:
    records = [_sft("x"), _sft("y")]
    b1 = export_sft(records)
    b2 = export_sft([_sft("x"), _sft("y")])
    assert b1.files["sft_messages.jsonl"] == b2.files["sft_messages.jsonl"]
    assert (
        b1.manifest["files"]["sft_messages.jsonl"]
        == (b2.manifest["files"]["sft_messages.jsonl"])
    )


def test_sft_export_rejects_empty() -> None:
    with pytest.raises(LlmExportWriterError, match="至少需要 1 条"):
        export_sft([])


# ── DPO writer ───────────────────────────────────────────


def test_dpo_export_string_and_conversational_forms() -> None:
    plain = export_dpo([_pref("p1")])
    docs = parse_dpo_bundle(plain)
    assert docs[0]["prompt"] == "解释递归"
    assert docs[0]["chosen"] == "递归是函数调用自身…"

    conv = export_dpo([_pref("p2")], conversational=True)
    cdocs = parse_dpo_bundle(conv, conversational=True)
    assert cdocs[0]["prompt"] == [{"role": "user", "content": "解释递归"}]
    assert cdocs[0]["chosen"][0]["role"] == "assistant"

    assert plain.manifest["conversational"] is False
    assert conv.manifest["conversational"] is True


def test_dpo_sidecar_holds_rationale_not_training_file() -> None:
    bundle = export_dpo([_pref("p1", rationale="更好")])
    main_raw = bundle.files["preference_dpo.jsonl"].decode("utf-8")
    assert "更好" not in main_raw  # rationale 不进训练文件
    prov = json.loads(bundle.files["provenance.jsonl"].decode("utf-8").splitlines()[0])
    assert prov["rationale"] == "更好"
    assert prov["presented_order"] == ["A", "B"]


def test_dpo_export_is_byte_identical_for_same_input() -> None:
    b1 = export_dpo([_pref("p1"), _pref("p2", winner="B")])
    b2 = export_dpo([_pref("p1"), _pref("p2", winner="B")])
    assert b1.files["preference_dpo.jsonl"] == b2.files["preference_dpo.jsonl"]


# ── 红线 4：不得混批 ─────────────────────────────────────


def test_sft_and_preference_cannot_be_mixed() -> None:
    with pytest.raises(LlmExportError, match="不得混"):
        assert_single_kind([_sft()], [_pref()])
    with pytest.raises(LlmExportError, match="之一"):
        assert_single_kind(None, None)
    assert_single_kind([_sft()], None)
    assert_single_kind(None, [_pref()])


# ── round-trip 与脱敏 ────────────────────────────────────


def test_roundtrip_and_hash_consistency() -> None:
    bundle = export_dpo([_pref("p1"), _pref("p2", winner="B")])
    verify_manifest(bundle)
    docs = parse_dpo_bundle(bundle)
    assert len(docs) == 2
    check_provenance_alignment(bundle, rows=2)


def test_provenance_sidecar_detects_raw_identity_leak() -> None:
    bundle = export_sft([_sft("alice-raw-42")])
    assert_no_pii(bundle)  # 未声明禁用词时通过
    with pytest.raises(LlmExportError, match="泄漏"):
        assert_no_pii(bundle, forbidden=["alice-raw-42"])


def test_all_excluded_export_raises() -> None:
    with pytest.raises(LlmExportWriterError, match="无可导出"):
        export_dpo([_pref("x", winner="TIE")])
