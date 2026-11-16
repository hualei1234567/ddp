"""N3：切片规则注册表、参数校验与确定性。

覆盖蓝图 N3 的「切片规则可配置、可版本化」：规则必须能被"按版本号定位"、
参数形状由 schema 约束、未注册版本显式失败、同输入同输出。
"""

from __future__ import annotations

import pytest
from app.domain.episodes.errors import (
    DomainValidationError,
    SegmentationRuleNotRegisteredError,
)
from app.domain.episodes.models import Segmentation
from app.domain.episodes.segmentation_rules import (
    EPISODE_BOUNDARY_RULE_VERSION,
    FIXED_WINDOW_RULE_VERSION,
    MAX_WINDOWS_PER_INPUT,
    SegmentationInput,
    SegmentationRule,
    SegmentationWindow,
    build_segmentation_rule_registry,
)


def _input(
    start: int = 0,
    end: int = 100,
    *,
    boundaries: tuple[tuple[int, int], ...] = (),
) -> SegmentationInput:
    return SegmentationInput(
        start_time_ns=start,
        end_time_ns=end,
        source_episode_boundaries=boundaries,
    )


def _windows(windows: tuple[SegmentationWindow, ...]) -> list[tuple[int, int]]:
    return [(item.start_time_ns, item.end_time_ns) for item in windows]


# ---------------------------------------------------------------------------
# 注册表：注册 / 查询 / 重复 / 未注册
# ---------------------------------------------------------------------------


def test_builtin_registry_exposes_two_rules() -> None:
    registry = build_segmentation_rule_registry()
    assert registry.registered_versions() == (
        EPISODE_BOUNDARY_RULE_VERSION,
        FIXED_WINDOW_RULE_VERSION,
    )
    # 列表顺序与注册顺序无关，按 rule_version 排序，保证输出稳定。
    listed = [rule.rule_version for rule in registry.list_rules()]
    assert listed == sorted(listed)
    assert len(registry) == 2


def test_builtin_registry_is_a_fresh_instance_per_call() -> None:
    """实例级注册表：两次构建互不影响，避免测试间/装配点间串味。"""
    first = build_segmentation_rule_registry()
    second = build_segmentation_rule_registry()
    assert first is not second
    custom = SegmentationRule(
        rule_id="custom",
        version="1.0.0",
        description="仅在第一个实例注册",
        parameters_schema={"type": "object", "properties": {}},
        apply=lambda segment_input, parameters: (
            SegmentationWindow(segment_input.start_time_ns, segment_input.end_time_ns),
        ),
    )
    first.register(custom)
    assert first.has("custom/1.0.0")
    assert not second.has("custom/1.0.0")


def test_register_rejects_duplicate_version() -> None:
    registry = build_segmentation_rule_registry()
    with pytest.raises(DomainValidationError) as excinfo:
        registry.register(registry.get(FIXED_WINDOW_RULE_VERSION))
    assert "已注册" in str(excinfo.value)


def test_unregistered_version_raises_instead_of_returning_none() -> None:
    registry = build_segmentation_rule_registry()
    with pytest.raises(SegmentationRuleNotRegisteredError) as excinfo:
        registry.get("no-such-rule/9.9.9")
    error = excinfo.value
    assert error.rule_version == "no-such-rule/9.9.9"
    # 报错必须能自证"当前有哪些合法取值"，否则客户端只能猜。
    assert error.details["registered_rule_versions"] == list(registry.registered_versions())
    # 沿用 validation_failed，不新增契约外错误码。
    assert error.code == "validation_failed"
    # 空串/非字符串同样显式失败，不静默通过。
    for bad in ("", "   ", None, 123):
        with pytest.raises(SegmentationRuleNotRegisteredError):
            registry.get(bad)  # type: ignore[arg-type]


def test_registry_rejects_invalid_rule_definition() -> None:
    noop = lambda _input, _params: ()  # noqa: E731
    with pytest.raises(DomainValidationError):
        SegmentationRule(
            rule_id="Bad_Id",
            version="1.0.0",
            description="非法标识",
            parameters_schema={"type": "object"},
            apply=noop,
        )
    with pytest.raises(DomainValidationError):
        SegmentationRule(
            rule_id="good",
            version="1.0",
            description="非法版本",
            parameters_schema={"type": "object"},
            apply=noop,
        )
    with pytest.raises(DomainValidationError):
        SegmentationRule(
            rule_id="good",
            version="1.0.0",
            description="",
            parameters_schema={"type": "object"},
            apply=noop,
        )
    with pytest.raises(DomainValidationError):
        # schema 本身非法（type 取值不在 JSON Schema 里）必须在注册时就拒绝，
        # 而不是等第一次调用才发现。
        SegmentationRule(
            rule_id="good",
            version="1.0.0",
            description="非法 schema",
            parameters_schema={"type": "not-a-json-type"},
            apply=noop,
        )


# ---------------------------------------------------------------------------
# 参数 schema 校验
# ---------------------------------------------------------------------------


def test_parameter_schema_rejects_missing_and_unknown_keys() -> None:
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    with pytest.raises(DomainValidationError) as excinfo:
        rule.validate_parameters({})
    assert "window_ns" in str(excinfo.value)
    assert excinfo.value.details["field_path"] == []

    with pytest.raises(DomainValidationError) as excinfo:
        rule.validate_parameters({"window_ns": 10, "unexpected": True})
    assert "unexpected" in str(excinfo.value)

    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"window_ns": 0})
    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"window_ns": True})
    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"window_ns": "10"})
    with pytest.raises(DomainValidationError):
        rule.validate_parameters(["not", "an", "object"])


def test_parameter_schema_reports_field_path() -> None:
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    with pytest.raises(DomainValidationError) as excinfo:
        rule.validate_parameters({"window_ns": -5})
    assert excinfo.value.details["field_path"] == ["window_ns"]
    assert excinfo.value.details["rule_version"] == FIXED_WINDOW_RULE_VERSION


def test_episode_boundary_rule_validates_parameters() -> None:
    """必填 boundary_source 缺一不可；未知参数必须拒绝，否则拼写错误被静默忽略。"""
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    assert rule.validate_parameters({"boundary_source": "PARSER"}) == {"boundary_source": "PARSER"}
    with pytest.raises(DomainValidationError) as excinfo:
        rule.validate_parameters({})
    assert "boundary_source" in str(excinfo.value)
    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"boundary_source": "SOMEWHERE"})
    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"boundary_source": "PARSER", "window_ns": 10})
    with pytest.raises(DomainValidationError):
        rule.validate_parameters({"boundary_source": "PARSER", "min_duration_ns": -1})


def test_episode_boundary_filters_short_fragments() -> None:
    """min_duration_ns 过滤解析产生的碎片片段；默认不丢任何片段。"""
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    boundaries = ((0, 5), (5, 500), (500, 900))
    kept = _windows(
        rule.execute(_input(0, 900, boundaries=boundaries), {"boundary_source": "PARSER"})
    )
    assert kept == [(0, 5), (5, 500), (500, 900)]
    filtered = _windows(
        rule.execute(
            _input(0, 900, boundaries=boundaries),
            {"boundary_source": "PARSER", "min_duration_ns": 100},
        )
    )
    assert filtered == [(5, 500), (500, 900)]
    with pytest.raises(DomainValidationError):
        # 阈值把全部片段滤光同样不是"成功的空结果"。
        rule.execute(
            _input(0, 900, boundaries=boundaries),
            {"boundary_source": "PARSER", "min_duration_ns": 10_000},
        )


# ---------------------------------------------------------------------------
# fixed-window/1.0.0 切分结果
# ---------------------------------------------------------------------------


def test_fixed_window_splits_evenly() -> None:
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(0, 100), {"window_ns": 25})
    assert _windows(windows) == [(0, 25), (25, 50), (50, 75), (75, 100)]
    assert [item.duration_ns for item in windows] == [25, 25, 25, 25]


def test_fixed_window_trims_first_and_last_window() -> None:
    """起止不落在窗口边界上：首尾裁切，中间保持整窗，且不越出输入区间。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(10, 200), {"window_ns": 100, "anchor_ns": 0})
    assert _windows(windows) == [(10, 100), (100, 200)]
    assert windows[0].duration_ns == 90
    assert sum(item.duration_ns for item in windows) == 190


def test_fixed_window_anchor_defaults_to_input_start() -> None:
    """不给锚点时窗口对齐输入起点：首片总是整窗，只有末片可能被裁。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(10, 200), {"window_ns": 100})
    assert _windows(windows) == [(10, 110), (110, 200)]
    assert [item.duration_ns for item in windows] == [100, 90]


def test_fixed_window_respects_anchor() -> None:
    """锚点让窗口边界对齐到固定网格，而不是跟着每次输入起点漂移。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(30, 130), {"window_ns": 50, "anchor_ns": 0})
    assert _windows(windows) == [(30, 50), (50, 100), (100, 130)]


def test_fixed_window_is_deterministic() -> None:
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    first = rule.execute(_input(7, 999), {"window_ns": 128})
    second = rule.execute(_input(7, 999), {"window_ns": 128})
    assert _windows(first) == _windows(second)
    # 换一个注册表实例也必须一致：规则不持有任何实例状态。
    other = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    assert _windows(other.execute(_input(7, 999), {"window_ns": 128})) == _windows(first)


def test_fixed_window_rejects_unbounded_window_count() -> None:
    """窗口数是资源边界：极端参数必须显式失败，而不是把进程拖死。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    with pytest.raises(DomainValidationError) as excinfo:
        rule.execute(_input(0, MAX_WINDOWS_PER_INPUT + 10), {"window_ns": 1})
    assert "上限" in str(excinfo.value)


def test_fixed_window_output_covers_input_without_overlap() -> None:
    """结构性不变量：窗口有序、不重叠、总时长等于输入时长。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(11, 3_017), {"window_ns": 500})
    for previous, current in zip(windows, windows[1:], strict=False):
        assert previous.end_time_ns == current.start_time_ns
    assert windows[0].start_time_ns == 11
    assert windows[-1].end_time_ns == 3_017
    assert sum(item.duration_ns for item in windows) == 3_017 - 11


# ---------------------------------------------------------------------------
# episode-boundary/1.0.0 切分结果
# ---------------------------------------------------------------------------


def test_episode_boundary_uses_source_boundaries() -> None:
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    windows = rule.execute(
        _input(0, 1_000, boundaries=((0, 300), (300, 700), (700, 1_000))),
        {"boundary_source": "PARSER"},
    )
    assert _windows(windows) == [(0, 300), (300, 700), (700, 1_000)]


def test_episode_boundary_clips_to_input_range() -> None:
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    windows = rule.execute(
        _input(100, 600, boundaries=((0, 300), (300, 700))),
        {"boundary_source": "MANIFEST"},
    )
    assert _windows(windows) == [(100, 300), (300, 600)]


def test_episode_boundary_rejects_empty_or_conflicting_input() -> None:
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    with pytest.raises(DomainValidationError):
        rule.execute(_input(0, 100, boundaries=()), {"boundary_source": "PARSER"})
    with pytest.raises(DomainValidationError):
        # 全部边界落在输入区间外：不是"空结果是成功"，而是输入不匹配。
        rule.execute(_input(1_000, 2_000, boundaries=((0, 100),)), {"boundary_source": "PARSER"})
    with pytest.raises(DomainValidationError):
        # 乱序/重叠边界不允许，否则切片结果不可复现。
        rule.execute(
            _input(0, 1_000, boundaries=((0, 500), (400, 900))),
            {"boundary_source": "PARSER"},
        )
    with pytest.raises(DomainValidationError):
        rule.execute(_input(0, 1_000, boundaries=((500, 500),)), {"boundary_source": "PARSER"})


def test_episode_boundary_is_deterministic() -> None:
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    boundaries = ((0, 120), (120, 480), (480, 900))
    params = {"boundary_source": "EXTERNAL"}
    assert _windows(rule.execute(_input(0, 900, boundaries=boundaries), params)) == _windows(
        rule.execute(_input(0, 900, boundaries=boundaries), params)
    )


# ---------------------------------------------------------------------------
# 边界与退化输入
# ---------------------------------------------------------------------------


def test_episode_boundary_min_duration_zero_keeps_every_fragment() -> None:
    """阈值 0 的语义是"不丢弃"，而不是"丢弃零时长"。

    用 ``if min_duration_ns and ...`` 而不写 ``if min_duration_ns > 0`` 很容易
    写成 ``... is not None`` 之类的变体，一旦判据变成"字段存在就过滤"，默认值 0
    会把全部片段滤光并报"没有交集"——表现为默认参数就不可用。
    """
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    boundaries = ((0, 1), (1, 2), (2, 100))
    for parameters in (
        {"boundary_source": "PARSER"},
        {"boundary_source": "PARSER", "min_duration_ns": 0},
    ):
        assert _windows(rule.execute(_input(0, 100, boundaries=boundaries), parameters)) == [
            (0, 1),
            (1, 2),
            (2, 100),
        ]


def test_single_frame_segment_survives_at_exact_min_duration() -> None:
    """单纳秒（单帧）片段：阈值取等于时长时保留，取时长 +1 时丢弃。

    ``min_duration_ns`` 用的是 ``<`` 而不是 ``<=``——边界取等号必须保留，
    否则"最短有效片段 = 阈值"这一档会被静默吃掉一帧。
    """
    rule = build_segmentation_rule_registry().get(EPISODE_BOUNDARY_RULE_VERSION)
    boundaries = ((0, 1), (1, 101))
    kept = rule.execute(
        _input(0, 101, boundaries=boundaries),
        {"boundary_source": "PARSER", "min_duration_ns": 1},
    )
    assert _windows(kept) == [(0, 1), (1, 101)]

    dropped = rule.execute(
        _input(0, 101, boundaries=boundaries),
        {"boundary_source": "PARSER", "min_duration_ns": 2},
    )
    assert _windows(dropped) == [(1, 101)]


def test_single_window_when_input_shorter_than_window() -> None:
    """输入比窗口还短：只产出一片被裁到输入的窗口，不产出空窗口。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(0, 3), {"window_ns": 1_000})
    assert _windows(windows) == [(0, 3)]
    assert windows[0].duration_ns == 3


def test_fixed_window_at_exact_resource_limit_is_allowed() -> None:
    """上限是"最多允许多少"，恰好等于上限必须通过（差一错误的高发点）。"""
    rule = build_segmentation_rule_registry().get(FIXED_WINDOW_RULE_VERSION)
    windows = rule.execute(_input(0, MAX_WINDOWS_PER_INPUT), {"window_ns": 1})
    assert len(windows) == MAX_WINDOWS_PER_INPUT
    assert windows[0].start_time_ns == 0
    assert windows[-1].end_time_ns == MAX_WINDOWS_PER_INPUT


def test_source_boundaries_outside_int64_are_rejected() -> None:
    """边界必须在 int64 内：越界值被裁切后会变成"看起来正常"的窗口。

    Python 整数无上限，``10**30`` 能过 ``isinstance(int)``。若不拦下，
    ``min(end, 10**30)`` 会把脏边界静默裁成一条合法窗口——脏数据伪装成正确结果。
    """
    with pytest.raises(DomainValidationError) as excinfo:
        SegmentationInput(
            start_time_ns=0,
            end_time_ns=10,
            source_episode_boundaries=((0, 2**63),),
        )
    assert "source_episode_boundaries[0].end_time_ns" in str(excinfo.value)

    with pytest.raises(DomainValidationError):
        SegmentationInput(
            start_time_ns=0,
            end_time_ns=10,
            source_episode_boundaries=((-(2**63) - 1, 10),),
        )


def test_rule_set_version_bump_coexists_with_previous_version() -> None:
    """规则集版本演进：同 rule_id 的新版本可与旧版本并存，按注册键精确命中。

    这是"可版本化"的核心性质——冻结在 Episode 上的旧 ``rule_version`` 必须在
    新规则上线后仍能解析，否则历史 Episode 的切片口径永久丢失。
    """
    registry = build_segmentation_rule_registry()
    previous = registry.get(FIXED_WINDOW_RULE_VERSION)
    successor = SegmentationRule(
        rule_id=previous.rule_id,
        version="1.1.0",
        description="同 rule_id 的下一个版本",
        parameters_schema=previous.parameters_schema,
        apply=previous.apply,
    )
    registry.register(successor)

    # 两个版本同时可解析，且注册键互不相同。
    assert registry.get(FIXED_WINDOW_RULE_VERSION) is previous
    assert registry.get("fixed-window/1.1.0") is successor
    assert registry.registered_versions() == (
        EPISODE_BOUNDARY_RULE_VERSION,
        FIXED_WINDOW_RULE_VERSION,
        "fixed-window/1.1.0",
    )
    # 新版本不能覆盖旧版本的注册键，重复注册同一版本仍然失败。
    with pytest.raises(DomainValidationError):
        registry.register(successor)
    # 未注册的中间版本仍然显式失败，不会被"就近"解析到相邻版本。
    with pytest.raises(SegmentationRuleNotRegisteredError):
        registry.get("fixed-window/1.0.1")


# ---------------------------------------------------------------------------
# SegmentationInput 形状校验
# ---------------------------------------------------------------------------


def test_segmentation_input_validates_shape() -> None:
    with pytest.raises(DomainValidationError):
        SegmentationInput(start_time_ns=100, end_time_ns=100)
    with pytest.raises(DomainValidationError):
        SegmentationInput(
            start_time_ns=0,
            end_time_ns=100,
            source_episode_boundaries=((0, 10, 20),),  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        SegmentationInput(
            start_time_ns=0,
            end_time_ns=100,
            source_episode_boundaries=(("0", "10"),),  # type: ignore[arg-type]
        )


def test_segmentation_domain_rule_version_matches_registry_key_format() -> None:
    """注册键与 ``Segmentation.rule_version`` 同域：都能存进 String(64) 且非空。

    ``Segmentation`` 的既有冻结约束要求 ``RULE`` 提供非空 ``rule_parameters``
    （阶段 2 契约冻结文档 §3.5），因此这里用各规则自己的合法参数集合。
    """
    registry = build_segmentation_rule_registry()
    parameters_by_rule = {
        FIXED_WINDOW_RULE_VERSION: {"window_ns": 50},
        EPISODE_BOUNDARY_RULE_VERSION: {"boundary_source": "PARSER"},
    }
    for rule in registry.list_rules():
        model = Segmentation(
            method="RULE",
            rule_version=rule.rule_version,
            start_time_ns=0,
            end_time_ns=100,
            rule_parameters=parameters_by_rule[rule.rule_version],
        )
        assert model.rule_version == rule.rule_version
        assert len(model.rule_version) <= 64
        # 注册键必须与实体字段一一对应，不能只在注册表里存在。
        assert registry.get(model.rule_version).rule_id == rule.rule_id
