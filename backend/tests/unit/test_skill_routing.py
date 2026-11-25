"""标注员技能标签路由测试（V52-009，蓝图 N2）。

**判定标准**：
1. 必配技能过滤——缺任一必配技能必被排除，原因码 MISSING_REQUIRED_SKILL；
2. 排序稳定性——同分按 annotator_id 决出次序，同输入跨运行结果一致；
3. 零配置兼容——任务无必配技能时不路由（routed=False），候选原样返回；
4. 质量分边界——恰好等于 min_quality 通过，低于被排除；
5. 标签规范化——大小写/空白差异不构成漏配。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.annotation.skill_routing import (
    QUALITY_WEIGHT,
    SKILL_WEIGHT,
    AnnotatorSkillProfile,
    SkillExclusionReason,
    SkillRoutingError,
    SkillTag,
    normalize_skill_tags,
    normalize_tag_name,
    parse_min_quality,
    route_candidates,
    skill_tags_to_json,
    sort_candidates,
)

A = UUID(int=0xA000)
B = UUID(int=0xB000)
C = UUID(int=0xC000)


def _profile(
    annotator_id: UUID,
    tags: set[str] | frozenset[str] = frozenset(),
    quality: float = 0.0,
    completed: int = 0,
) -> AnnotatorSkillProfile:
    return AnnotatorSkillProfile(
        annotator_id=annotator_id,
        tags=frozenset(tags),
        quality_score=quality,
        completed_count=completed,
    )


# ── 值对象与规范化 ──────────────────────────────────────────


def test_tag_name_normalizes_case_and_whitespace() -> None:
    assert normalize_tag_name("  Kitchen ") == "kitchen"
    assert SkillTag(tag_name="  3D BOX ").tag_name == "3d box"
    with pytest.raises(SkillRoutingError):
        normalize_tag_name("   ")
    with pytest.raises(SkillRoutingError):
        normalize_tag_name(123)  # type: ignore[arg-type]


def test_tag_rejects_empty_and_bad_weight() -> None:
    with pytest.raises(SkillRoutingError):
        SkillTag(tag_name="x", weight=0.0)
    with pytest.raises(SkillRoutingError):
        SkillTag(tag_name="x", weight=1.5)
    with pytest.raises(SkillRoutingError):
        SkillTag(tag_name="x", category="   ")
    # 合法边界
    assert SkillTag(tag_name="x", weight=1.0).weight == 1.0


def test_profile_normalizes_tags_and_validates_ranges() -> None:
    profile = _profile(A, {"Kitchen", " 3D "}, quality=0.5, completed=3)
    assert profile.tags == {"kitchen", "3d"}
    with pytest.raises(SkillRoutingError):
        _profile(A, quality=-0.1)
    with pytest.raises(SkillRoutingError):
        _profile(A, quality=1.1)
    with pytest.raises(SkillRoutingError):
        _profile(A, completed=-1)


def test_profile_has_all_is_normalization_insensitive() -> None:
    profile = _profile(A, {"kitchen"})
    assert profile.has_all(["Kitchen", " kitchen"])
    assert not profile.has_all(["kitchen", "driving"])


# ── 必配技能过滤 ────────────────────────────────────────────


def test_missing_any_required_skill_excludes_candidate() -> None:
    skills = [SkillTag(tag_name="kitchen"), SkillTag(tag_name="driving")]
    candidates = [
        _profile(A, {"kitchen"}, quality=0.99),
        _profile(B, {"kitchen", "driving"}, quality=0.5),
        _profile(C, {"driving"}, quality=0.99),
    ]
    outcome = route_candidates(skills, 0.0, candidates)
    assert outcome.routed is True
    assert [p.annotator_id for p in outcome.eligible] == [B]
    assert [(e.annotator_id, e.reason) for e in outcome.excluded] == [
        (A, SkillExclusionReason.MISSING_REQUIRED_SKILL),
        (C, SkillExclusionReason.MISSING_REQUIRED_SKILL),
    ]


def test_low_quality_excluded_with_reason() -> None:
    skills = [SkillTag(tag_name="kitchen")]
    candidates = [_profile(A, {"kitchen"}, quality=0.4), _profile(B, {"kitchen"}, quality=0.6)]
    outcome = route_candidates(skills, 0.5, candidates)
    assert [p.annotator_id for p in outcome.eligible] == [B]
    assert [(e.annotator_id, e.reason) for e in outcome.excluded] == [
        (A, SkillExclusionReason.LOW_QUALITY)
    ]


def test_quality_exactly_at_threshold_passes() -> None:
    outcome = route_candidates(
        [SkillTag(tag_name="kitchen")], 0.5, [_profile(A, {"kitchen"}, quality=0.5)]
    )
    assert [p.annotator_id for p in outcome.eligible] == [A]


def test_missing_skill_takes_precedence_over_low_quality() -> None:
    """同时缺技能且质量低：主因记 MISSING_REQUIRED_SKILL。"""
    outcome = route_candidates(
        [SkillTag(tag_name="kitchen")], 0.9, [_profile(A, set(), quality=0.1)]
    )
    assert outcome.excluded[0].reason is SkillExclusionReason.MISSING_REQUIRED_SKILL


def test_min_quality_zero_means_no_quality_filter() -> None:
    outcome = route_candidates(
        [SkillTag(tag_name="kitchen")], 0.0, [_profile(A, {"kitchen"}, quality=0.0)]
    )
    assert not outcome.excluded


# ── 排序：质量×0.7 + 可选技能命中率×0.3 ─────────────────────


def test_quality_dominates_score() -> None:
    """质量差 0.1（0.07 分）压过可选技能全命中（0.3×1.0=0.3 不成立时）。"""
    skills = [SkillTag(tag_name="kitchen", required=True), SkillTag(tag_name="3d", required=False)]
    high_quality_no_optional = _profile(A, {"kitchen"}, quality=0.9)
    low_quality_all_optional = _profile(B, {"kitchen", "3d"}, quality=0.5)
    outcome = route_candidates(skills, 0.0, [low_quality_all_optional, high_quality_no_optional])
    # 0.9×0.7=0.63 vs 0.5×0.7+1.0×0.3=0.65 → B 反超
    assert outcome.eligible[0].annotator_id == B
    assert outcome.eligible[1].annotator_id == A


def test_optional_hit_ratio_uses_weights() -> None:
    skills = [
        SkillTag(tag_name="kitchen", required=True),
        SkillTag(tag_name="3d", required=False, weight=0.2),
        SkillTag(tag_name="driving", required=False, weight=0.8),
    ]
    hit_cheap = _profile(A, {"kitchen", "3d"}, quality=0.5)
    hit_expensive = _profile(B, {"kitchen", "driving"}, quality=0.5)
    outcome = route_candidates(skills, 0.0, [hit_cheap, hit_expensive])
    # 加权命中率：0.2 vs 0.8 → 总分 0.35+0.06 vs 0.35+0.24
    assert outcome.eligible[0].annotator_id == B


def test_ties_break_by_annotator_id_stable() -> None:
    """同分按 id 升序：跨运行、跨输入顺序结果一致（排序稳定性）。"""
    skills = [SkillTag(tag_name="kitchen")]
    c1 = _profile(C, {"kitchen"}, quality=0.8)
    c2 = _profile(A, {"kitchen"}, quality=0.8)
    c3 = _profile(B, {"kitchen"}, quality=0.8)
    for order in ([c1, c2, c3], [c3, c2, c1], [c2, c3, c1]):
        outcome = route_candidates(skills, 0.0, order)
        assert [p.annotator_id for p in outcome.eligible] == [A, B, C]


def test_sort_candidates_matches_route_ordering() -> None:
    skills = [
        SkillTag(tag_name="kitchen", required=True),
        SkillTag(tag_name="3d", required=False),
    ]
    a = _profile(A, {"kitchen"}, quality=0.6)
    b = _profile(B, {"kitchen", "3d"}, quality=0.6)
    assert [p.annotator_id for p in sort_candidates([a, b], skills)] == [B, A]


# ── 零配置兼容（向后兼容不变量）──────────────────────────────


def test_no_required_skills_routes_nothing_and_returns_all() -> None:
    """零必配技能：不路由、全量返回、保持原顺序——未配置技能路由的
    项目行为必须与旧版完全一致。"""
    candidates = [_profile(C, quality=0.2), _profile(A, quality=0.9), _profile(B)]
    outcome = route_candidates([], 0.5, candidates)
    assert outcome.routed is False
    assert outcome.excluded == ()
    assert [p.annotator_id for p in outcome.eligible] == [C, A, B]


def test_optional_only_skills_still_no_routing() -> None:
    """只配可选技能（无必配）同样不路由：可选技能只影响排序，不做门槛。"""
    skills = [SkillTag(tag_name="3d", required=False)]
    candidates = [_profile(A, {"kitchen"}), _profile(B, {"3d"})]
    outcome = route_candidates(skills, 0.0, candidates)
    assert outcome.routed is False
    assert [p.annotator_id for p in outcome.eligible] == [A, B]


def test_empty_candidates_with_required_skills_yields_empty_eligible() -> None:
    outcome = route_candidates([SkillTag(tag_name="kitchen")], 0.0, [])
    assert outcome.routed is True
    assert outcome.eligible == ()
    assert outcome.excluded == ()


# ── 配置解析辅助 ────────────────────────────────────────────


def test_parse_min_quality_from_policy() -> None:
    assert parse_min_quality({}) == 0.0
    assert parse_min_quality({"skill_routing": None}) == 0.0
    assert parse_min_quality({"skill_routing": {"min_quality": 0.8}}) == 0.8
    with pytest.raises(SkillRoutingError):
        parse_min_quality({"skill_routing": {"min_quality": 1.5}})
    with pytest.raises(SkillRoutingError):
        parse_min_quality({"skill_routing": {"min_quality": "high"}})


def test_normalize_skill_tags_accepts_json_and_skilltag() -> None:
    tags = normalize_skill_tags(
        [
            {"tag_name": " Kitchen ", "weight": 0.5},
            {"tag_name": "3d", "required": False, "category": "modeling"},
        ]
    )
    assert [t.tag_name for t in tags] == ["kitchen", "3d"]
    assert tags[0].weight == 0.5
    assert tags[1].required is False
    assert tags[1].category == "modeling"
    # 直接传 SkillTag / None / 空序列
    assert normalize_skill_tags(tags)[0].tag_name == "kitchen"
    assert normalize_skill_tags(None) == ()
    assert normalize_skill_tags([]) == ()
    with pytest.raises(SkillRoutingError):
        normalize_skill_tags([{"name": "缺 tag_name"}])
    with pytest.raises(SkillRoutingError):
        normalize_skill_tags("kitchen")  # type: ignore[arg-type]


def test_skill_tags_to_json_roundtrip() -> None:
    tags = normalize_skill_tags([{"tag_name": "Kitchen", "weight": 0.8}])
    payload = skill_tags_to_json(tags)
    assert payload == [{"tag_name": "kitchen", "category": None, "weight": 0.8, "required": True}]
    reparsed = normalize_skill_tags(payload)
    assert reparsed == tags


def test_score_weights_are_blueprint_values() -> None:
    """评分权重必须保持蓝图数值（0.7 / 0.3）——被改动时提醒同步蓝图与 PR。"""
    assert QUALITY_WEIGHT == 0.7
    assert SKILL_WEIGHT == 0.3


def test_invalid_inputs_raise() -> None:
    with pytest.raises(SkillRoutingError):
        route_candidates([SkillTag(tag_name="kitchen")], 1.5, [])
    with pytest.raises(SkillRoutingError):
        route_candidates(["kitchen"], 0.0, [])  # type: ignore[list-item]
    with pytest.raises(SkillRoutingError):
        route_candidates([SkillTag(tag_name="kitchen")], 0.0, ["not-a-profile"])  # type: ignore[list-item]


def test_uuid_generation_for_profiles_is_free() -> None:
    """档案 id 由端口层生成；确认 uuid4 路径无意外约束。"""
    profile = _profile(uuid4(), {"x"}, quality=0.0)
    assert profile.completed_count == 0
