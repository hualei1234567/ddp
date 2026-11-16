"""切片规则的适用范围与参数校验（N3 接入点）。

为什么校验放在应用层而不是 ``Segmentation.__post_init__``：领域模型不应持有
规则注册表（那会把"注册了什么规则"变成实体的隐式全局依赖），而"这次请求
用的规则版本是否存在于本服务已注册集合"取决于装配时的注册表实例，
属于用例层的输入校验。

**只校验 ``method=RULE``**：``MANUAL_TIME_RANGE`` 的 ``rule_version`` 是
人工切片配置的标签（既有 API/前端/验收证据全部使用 ``seg-manual-v1``），
它背后没有规则体，也没有 ``rule_parameters``。如果对人工切片也强制要求
注册，会把已冻结的既有创建路径全部打断，且没有换来任何可追溯性——
人工区间的可追溯性来自 Episode 自身的起止时间与审计。
"""

from __future__ import annotations

from app.domain.episodes.models import Segmentation, SegmentationMethod
from app.domain.episodes.segmentation_rules import (
    SegmentationRule,
    SegmentationRuleRegistry,
)


def validate_segmentation_rule(
    segmentation: Segmentation,
    registry: SegmentationRuleRegistry,
) -> SegmentationRule | None:
    """校验规则版本已注册且参数合法，返回对应规则；人工切片返回 ``None``。

    失败时抛 ``DomainValidationError``（未注册版本是其子类
    ``SegmentationRuleNotRegisteredError``），并带上 ``rule_version`` 与
    已注册版本清单，便于客户端与排障直接定位。
    """
    if segmentation.method is not SegmentationMethod.RULE:
        return None
    rule = registry.get(segmentation.rule_version)
    rule.validate_parameters(segmentation.rule_parameters)
    return rule


__all__ = ["validate_segmentation_rule"]
