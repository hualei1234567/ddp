"""N3：切片规则校验的用例层口径（纯函数，不落库）。

为什么校验只覆盖 ``method=RULE``：见 ``application/episodes/segmentation.py``
的模块说明。这里只验证判定口径，落库行为在
``tests/infrastructure/test_episode_segmentation_rules.py``。
"""

from __future__ import annotations

import pytest
from app.application.episodes.segmentation import validate_segmentation_rule
from app.domain.episodes.errors import (
    DomainValidationError,
    SegmentationRuleNotRegisteredError,
)
from app.domain.episodes.models import Segmentation
from app.domain.episodes.segmentation_rules import (
    EPISODE_BOUNDARY_RULE_VERSION,
    FIXED_WINDOW_RULE_VERSION,
    SegmentationRuleRegistry,
    build_segmentation_rule_registry,
)


def _rule_segmentation(rule_version: str, parameters: dict[str, object]) -> Segmentation:
    return Segmentation(
        method="RULE",
        rule_version=rule_version,
        start_time_ns=0,
        end_time_ns=1_000,
        rule_parameters=parameters,
    )


def test_manual_segmentation_is_not_required_to_be_registered() -> None:
    """人工时间范围切片的 ``rule_version`` 是配置标签，不是注册表键。

    既有 API/测试/验收证据全部使用 ``seg-manual-v1``；对人工切片强制注册会
    打断已冻结的创建路径，且换不来可追溯性——人工区间的可追溯性来自 Episode
    自身的起止时间与创建审计，而不是某条规则体。
    """
    manual = Segmentation(
        method="MANUAL_TIME_RANGE",
        rule_version="seg-manual-v1",
        start_time_ns=0,
        end_time_ns=1_000,
    )
    assert validate_segmentation_rule(manual, build_segmentation_rule_registry()) is None
    # 空注册表也不影响人工切片：校验逻辑根本不应触碰注册表。
    assert validate_segmentation_rule(manual, SegmentationRuleRegistry()) is None


def test_rule_segmentation_requires_registered_version() -> None:
    registry = build_segmentation_rule_registry()
    with pytest.raises(SegmentationRuleNotRegisteredError):
        validate_segmentation_rule(
            _rule_segmentation("not-registered/1.0.0", {"window_ns": 5}),
            registry,
        )


def test_rule_segmentation_validates_parameters_against_rule_schema() -> None:
    registry = build_segmentation_rule_registry()
    with pytest.raises(DomainValidationError) as excinfo:
        validate_segmentation_rule(
            _rule_segmentation(FIXED_WINDOW_RULE_VERSION, {"window_ns": 0}),
            registry,
        )
    assert excinfo.value.details["field_path"] == ["window_ns"]

    rule = validate_segmentation_rule(
        _rule_segmentation(FIXED_WINDOW_RULE_VERSION, {"window_ns": 100}),
        registry,
    )
    assert rule is not None
    assert rule.rule_version == FIXED_WINDOW_RULE_VERSION

    boundary_rule = validate_segmentation_rule(
        _rule_segmentation(EPISODE_BOUNDARY_RULE_VERSION, {"boundary_source": "PARSER"}),
        registry,
    )
    assert boundary_rule is not None
    assert boundary_rule.rule_version == EPISODE_BOUNDARY_RULE_VERSION


def test_rule_segmentation_rejects_parameters_for_wrong_rule() -> None:
    """参数必须与所声明的规则版本匹配，不能拿 A 规则的参数喂 B 规则。"""
    with pytest.raises(DomainValidationError):
        validate_segmentation_rule(
            _rule_segmentation(EPISODE_BOUNDARY_RULE_VERSION, {"window_ns": 100}),
            build_segmentation_rule_registry(),
        )


def test_empty_registry_rejects_every_rule_segmentation() -> None:
    with pytest.raises(SegmentationRuleNotRegisteredError):
        validate_segmentation_rule(
            _rule_segmentation(FIXED_WINDOW_RULE_VERSION, {"window_ns": 100}),
            SegmentationRuleRegistry(),
        )
