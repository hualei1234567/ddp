"""语义近似去重纯领域测试（V52-031，蓝图 G6 / N1 训练集防污染）。

覆盖：trigram 计算与对称性、阈值边界（>= 命中）、空文本、超长文本、
候选排序确定性、指纹格式、后端可插拔（VECTOR 不可用必须显式报错）。
全部不依赖 DB / 扩展 / Web。
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.semantic_dedup import (
    DEFAULT_THRESHOLD,
    MAX_CANDIDATE_LIMIT,
    MAX_TEXT_LENGTH,
    DedupBackend,
    DedupBackendUnavailableError,
    NearDuplicateCandidate,
    assert_backend_available,
    find_near_duplicates,
    normalize_text,
    text_digest,
    trigram_similarity,
    validate_candidate_limit,
    validate_threshold,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_BASE = "pick up the red cup from the kitchen table"
_NEAR = "pick up the red cup from the kitchen desk"
_FAR = "navigate to the charging station and dock"


def _candidate(text: str, *, score: float = 0.0) -> NearDuplicateCandidate:
    return NearDuplicateCandidate.from_text(text, score=score)


def test_identical_text_similarity_is_one() -> None:
    assert trigram_similarity(_BASE, _BASE) == 1.0


def test_unrelated_text_similarity_is_far_below_threshold() -> None:
    """无关句子仍可能共享 "the" 等短词三元组，但必须远低于命中阈值。"""
    score = trigram_similarity(_BASE, _FAR)
    assert 0.0 <= score < 0.2


def test_similarity_is_symmetric() -> None:
    forward = trigram_similarity(_BASE, _NEAR)
    backward = trigram_similarity(_NEAR, _BASE)
    assert forward == backward
    assert 0.0 < forward < 1.0


def test_short_text_uses_padding_trigrams() -> None:
    """短文本（<3 字符）按 pg_trgm 补位口径仍有可比 3-gram。"""
    assert trigram_similarity("a", "a") == 1.0
    assert trigram_similarity("ab", "ab") == 1.0
    assert trigram_similarity("ab", "ac") > 0.0


def test_empty_text_similarity_semantics() -> None:
    """空/空 = 1.0（完全一致）；空/非空 = 0.0；空白按空处理。"""
    assert trigram_similarity("", "") == 1.0
    assert trigram_similarity("", _BASE) == 0.0
    assert trigram_similarity("   ", _BASE) == 0.0


def test_long_text_similarity_supports_up_to_any_length() -> None:
    """纯函数本身不限长（载体列上限由 find/API 校验，见超长文本用例）。"""
    long_text = "pick the cup " * 400
    assert trigram_similarity(long_text, long_text) == 1.0


def test_unicode_text_similarity_supports_chinese() -> None:
    """Python 兜底按 Unicode alnum 切词，中文文本同样可比较（pg_trgm 有差异）。"""
    assert trigram_similarity("拿起红色杯子", "拿起红色杯子") == 1.0
    partial = trigram_similarity("拿起红色杯子", "拿起红色杯子放到桌上")
    assert 0.0 < partial < 1.0


def test_text_digest_format_and_normalization() -> None:
    digest = text_digest("  pick   up   the cup ")
    assert _DIGEST_RE.match(digest)
    # 空白结构不影响指纹：连续空白被压成单空格。
    assert digest == text_digest("pick up the cup")
    assert digest != text_digest("pick up the red cup")


def test_normalize_text_rejects_blank_and_overlong() -> None:
    assert normalize_text("  a  b ") == "a b"
    with pytest.raises(DomainValidationError):
        normalize_text("   ")
    with pytest.raises(DomainValidationError):
        normalize_text("x" * (MAX_TEXT_LENGTH + 1))


def test_find_near_duplicates_threshold_boundary_is_inclusive() -> None:
    """阈值边界语义：score == threshold 必须命中（>=），再加一点点就排除。"""
    candidate = _candidate(_NEAR)
    expected = trigram_similarity(_BASE, _NEAR)
    assert 0.0 < expected < 1.0

    hit = find_near_duplicates(_BASE, (candidate,), threshold=expected)
    assert len(hit) == 1
    assert hit[0].score == round(expected, 6)

    missed = find_near_duplicates(_BASE, (candidate,), threshold=expected + 0.000001)
    assert missed == ()


def test_find_near_duplicates_zero_threshold_matches_everything() -> None:
    candidates = (_candidate(_NEAR), _candidate(_FAR))
    matched = find_near_duplicates(_BASE, candidates, threshold=0.0)
    assert len(matched) == 2


def test_find_near_duplicates_one_threshold_only_identical() -> None:
    candidates = (_candidate(_BASE), _candidate(_NEAR))
    matched = find_near_duplicates(_BASE, candidates, threshold=1.0)
    assert [item.text for item in matched] == [_BASE]
    assert matched[0].score == 1.0


def test_find_near_duplicates_sorted_desc_and_deterministic() -> None:
    candidates = (
        _candidate(_FAR),
        _candidate(_NEAR),
        _candidate(_BASE),
    )
    matched = find_near_duplicates(_BASE, candidates, threshold=0.0)
    scores = [item.score for item in matched]
    assert scores == sorted(scores, reverse=True)
    again = find_near_duplicates(_BASE, candidates, threshold=0.0)
    assert [item.text_digest for item in again] == [item.text_digest for item in matched]


def test_find_near_duplicates_default_threshold_is_documented_value() -> None:
    assert DEFAULT_THRESHOLD == 0.85


def test_find_near_duplicates_rejects_blank_query() -> None:
    with pytest.raises(DomainValidationError):
        find_near_duplicates("   ", (_candidate(_BASE),))


def test_find_near_duplicates_rejects_overlong_query() -> None:
    with pytest.raises(DomainValidationError):
        find_near_duplicates("x" * (MAX_TEXT_LENGTH + 1), ())


def test_find_near_duplicates_accepts_max_length_query() -> None:
    text = "x" * MAX_TEXT_LENGTH
    assert find_near_duplicates(text, (_candidate(text),))[0].score == 1.0


def test_threshold_validation_range() -> None:
    assert validate_threshold(0) == 0.0
    assert validate_threshold(1.0) == 1.0
    with pytest.raises(DomainValidationError):
        validate_threshold(1.5)
    with pytest.raises(DomainValidationError):
        validate_threshold(-0.1)
    with pytest.raises(DomainValidationError):
        validate_threshold("0.9")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        validate_threshold(True)  # type: ignore[arg-type]


def test_candidate_limit_validation() -> None:
    assert validate_candidate_limit(1) == 1
    assert validate_candidate_limit(MAX_CANDIDATE_LIMIT) == MAX_CANDIDATE_LIMIT
    with pytest.raises(DomainValidationError):
        validate_candidate_limit(0)
    with pytest.raises(DomainValidationError):
        validate_candidate_limit(MAX_CANDIDATE_LIMIT + 1)
    with pytest.raises(DomainValidationError):
        validate_candidate_limit(True)  # type: ignore[arg-type]


def test_candidate_validation_and_ordering_fields() -> None:
    episode_id = uuid4()
    candidate = NearDuplicateCandidate.from_text(_BASE, score=0.5, episode_id=episode_id)
    assert candidate.text_digest == text_digest(_BASE)
    assert candidate.episode_id == episode_id
    with pytest.raises(DomainValidationError):
        _candidate(_BASE, score=1.5)
    with pytest.raises(DomainValidationError):
        NearDuplicateCandidate(text_digest="", text=_BASE, score=0.0)
    with pytest.raises(DomainValidationError):
        NearDuplicateCandidate(text_digest=text_digest(_BASE), text="   ", score=0.0)


def test_trigram_backend_is_available_regardless_of_vector_support() -> None:
    assert assert_backend_available(DedupBackend.TRIGRAM, False) is None
    assert assert_backend_available(DedupBackend.TRIGRAM, True) is None


def test_vector_backend_without_extension_raises_with_clear_reason() -> None:
    with pytest.raises(DedupBackendUnavailableError) as excinfo:
        assert_backend_available(DedupBackend.VECTOR, False)
    error = excinfo.value
    assert error.details is not None
    assert error.details["reason"] == "extension_missing"
    assert error.details["has_vector_extension"] is False
    assert "pgvector" in str(error)


def test_vector_backend_with_extension_still_raises_not_implemented() -> None:
    """扩展存在也不假装向量能力：代码未实现必须说清楚。"""
    with pytest.raises(DedupBackendUnavailableError) as excinfo:
        assert_backend_available("VECTOR", True)
    assert excinfo.value.details is not None
    assert excinfo.value.details["reason"] == "not_implemented"
    assert "未实现" in str(excinfo.value)


def test_unknown_backend_rejected() -> None:
    with pytest.raises(DomainValidationError):
        assert_backend_available("FAISS", False)  # type: ignore[arg-type]
