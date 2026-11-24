"""V51-010 证据定位的领域测试。

覆盖四类事实：

1. **纳秒精度**：定位区间必须用整数/十进制字符串承载，浮点会被静默改值；
2. **三态语义**：没有定位是 ``None``，不是"三个字段都为空的对象"；全空对象
   是缺陷数据，必须拒绝；
3. **单一事实来源**：``evidence["location"]`` 与 ``evidence_location`` 同时
   给出且不一致时失败，不允许两处各说各话；
4. **保留键不残留**：``location`` 被提升后不得继续留在 ``evidence`` 里。
"""

from __future__ import annotations

import pytest
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySeverity,
)
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.evidence import (
    EVIDENCE_LOCATION_KEY,
    EvidenceLocation,
    EvidenceTimeRange,
    parse_evidence_location,
    split_evidence_location,
)
from app.domain.quality.models import QualityFindingDraft

_NS = 1_763_510_400_000_000_000


def _draft(**overrides: object) -> QualityFindingDraft:
    payload: dict[str, object] = {
        "rule_id": "stream_sync_aligned",
        "rule_version": "1.1.0",
        "category": QualityCategory.TEMPORAL,
        "severity": QualitySeverity.ERROR,
        "status": QualityFindingStatus.FAIL,
        "message": "时间同步超差",
        "score": 0.0,
    }
    payload.update(overrides)
    return QualityFindingDraft(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 时间区间
# ---------------------------------------------------------------------------


def test_time_range_keeps_nanosecond_precision_as_strings() -> None:
    """纳秒超过 2^53：载荷必须是精确十进制字符串，不能退化成浮点。"""
    location = EvidenceLocation(
        time_range={"start_time_ns": _NS, "end_time_ns": _NS + 40_000_000},
    )

    payload = location.to_payload()

    assert payload["time_range"] == {
        "start_time_ns": str(_NS),
        "end_time_ns": str(_NS + 40_000_000),
    }
    # 回读后再转 int 必须与原始值逐位相同（字符串通道无精度损失）。
    assert int(payload["time_range"]["start_time_ns"]) == _NS


def test_time_range_rejects_reversed_or_empty_range() -> None:
    with pytest.raises(DomainValidationError):
        EvidenceTimeRange(start_time_ns=2, end_time_ns=1)
    with pytest.raises(DomainValidationError):
        EvidenceTimeRange(start_time_ns=1, end_time_ns=1)


def test_time_range_accepts_decimal_strings_and_rejects_junk() -> None:
    parsed = EvidenceTimeRange(start_time_ns="100", end_time_ns="200")
    assert parsed.start_time_ns == 100
    for bad in ("1e9", "0x10", "", " 12.5 "):
        with pytest.raises(DomainValidationError):
            EvidenceTimeRange(start_time_ns=bad, end_time_ns=200)


# ---------------------------------------------------------------------------
# EvidenceLocation
# ---------------------------------------------------------------------------


def test_location_requires_at_least_one_dimension() -> None:
    with pytest.raises(DomainValidationError):
        EvidenceLocation()


def test_location_accepts_each_single_dimension() -> None:
    assert EvidenceLocation(stream_id="main").stream_id == "main"
    assert EvidenceLocation(field_path="labels.action").field_path == "labels.action"
    assert EvidenceLocation(time_range=EvidenceTimeRange(1, 2)).has_time_range


def test_location_rejects_empty_string_dimensions() -> None:
    """空串会把"没有这一维"与"这一维是空值"混为一谈。"""
    with pytest.raises(DomainValidationError):
        EvidenceLocation(stream_id="")
    with pytest.raises(DomainValidationError):
        EvidenceLocation(field_path="   ")


def test_location_rejects_unknown_fields() -> None:
    with pytest.raises(DomainValidationError):
        parse_evidence_location({"stream_id": "main", "frame_index": 3})


def test_parse_location_returns_none_for_none() -> None:
    assert parse_evidence_location(None) is None


# ---------------------------------------------------------------------------
# 拆分保留键
# ---------------------------------------------------------------------------


def test_split_lifts_location_and_keeps_rest() -> None:
    location, rest = split_evidence_location(
        {
            EVIDENCE_LOCATION_KEY: {"stream_id": "wrist"},
            "tolerance_ns": 40_000_000,
        }
    )

    assert location is not None
    assert location.stream_id == "wrist"
    assert rest == {"tolerance_ns": 40_000_000}


def test_split_returns_none_rest_when_only_location_present() -> None:
    """只有定位时，"其余证据"是 None 而不是空对象：空对象会被读成有排障信息。"""
    location, rest = split_evidence_location(
        {EVIDENCE_LOCATION_KEY: {"field_path": "labels.action"}}
    )

    assert location is not None
    assert rest is None


def test_split_passthrough_when_no_location_key() -> None:
    location, rest = split_evidence_location({"threshold": 0.8})

    assert location is None
    assert rest == {"threshold": 0.8}


# ---------------------------------------------------------------------------
# 与 finding 的集成
# ---------------------------------------------------------------------------


def test_draft_promotes_inline_location_and_drops_reserved_key() -> None:
    draft = _draft(
        evidence={
            EVIDENCE_LOCATION_KEY: {
                "time_range": {"start_time_ns": _NS, "end_time_ns": _NS + 1},
                "stream_id": "main",
            },
            "max_start_offset_ns": 61_000_000,
        }
    )

    assert draft.evidence_location is not None
    assert draft.evidence_location.stream_id == "main"
    # 保留键被提升后不得继续留在 evidence 里（同一事实只存一处）。
    assert EVIDENCE_LOCATION_KEY not in (draft.evidence or {})
    assert draft.evidence == {"max_start_offset_ns": 61_000_000}


def test_draft_accepts_explicit_evidence_location() -> None:
    draft = _draft(evidence_location={"field_path": "labels.velocity"})

    assert draft.evidence_location is not None
    assert draft.evidence_location.field_path == "labels.velocity"
    assert draft.evidence is None


def test_draft_rejects_conflicting_location_sources() -> None:
    """两处定位不一致时失败：静默取一会让界面与服务端解释分叉。"""
    with pytest.raises(DomainValidationError):
        _draft(
            evidence={EVIDENCE_LOCATION_KEY: {"stream_id": "main"}},
            evidence_location={"stream_id": "wrist"},
        )


def test_draft_accepts_identical_location_in_both_places() -> None:
    draft = _draft(
        evidence={EVIDENCE_LOCATION_KEY: {"field_path": "labels.action"}},
        evidence_location={"field_path": "labels.action"},
    )

    assert draft.evidence_location is not None
    assert draft.evidence_location.field_path == "labels.action"


def test_draft_without_location_has_none() -> None:
    draft = _draft(evidence={"threshold": 0.8})

    assert draft.evidence_location is None
    assert draft.evidence == {"threshold": 0.8}
