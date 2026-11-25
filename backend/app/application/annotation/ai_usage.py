"""AI 建议登记的 Token 计量映射（V52-010）。

从 ``ai_suggestions.py`` 分出的理由：该文件已 499 行（500 行评审线），
计量映射是纯附加职责（把 ``command.usage`` 载荷转成领域计量记录），
与建议的状态机/权限/幂等没有共享状态。

职责边界：

- **token 数由调用方给出**（模型侧返回值），本模块不猜用量；
- 金额由领域 :func:`compute_cost` 按 ``price_map`` 折算，未配置价格的
  模型 ``cost_estimate=None``，不猜价；
- ``purpose`` 等合法性一律交给领域实体构造校验，不在这里复制枚举判断。

``recorder`` 为 ``None`` 时整个模块不动作——默认关闭、现有行为不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.annotation.errors import DomainValidationError, ResourceNotFoundError
from app.domain.annotation.token_usage import (
    TokenUsagePurpose,
    TokenUsageRecord,
    compute_cost,
)
from app.domain.identity.models import ProjectScope


class TokenUsageRecorder:
    """Token 计量回调协议（结构化类型，注入方按需实现）。

    应用层只依赖这个最小接口：登记一批计量行。**默认 None 时不记录、
    现有行为不变**——recorder 缺席是常态（大多数部署还没接计量），
    有它才写 ``ai_token_usage``。
    """

    def record(self, scope: ProjectScope, records: tuple[TokenUsageRecord, ...]) -> None:
        raise NotImplementedError


def _require_usage_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def usage_records_from(
    scope: ProjectScope,
    suggestions: tuple[AiSuggestion, ...],
    *,
    usage: tuple[Mapping[str, object], ...],
    price_map: dict[str, object] | None,
    now: datetime,
) -> tuple[TokenUsageRecord, ...]:
    """把登记载荷里的计量条目转成 :class:`TokenUsageRecord`。

    归属规则：条目带 ``annotation_id`` 时只计入该记录的建议；不带时计入
    整批。``purpose`` 缺省 ``PRE_LABEL``（建议登记的默认用途）。价格表为
    ``None`` 或未配置该模型时 ``cost_estimate=None``。
    """
    records: list[TokenUsageRecord] = []
    for item in usage:
        annotation_filter = item.get("annotation_id")
        matched = [
            suggestion
            for suggestion in suggestions
            if annotation_filter is None or str(suggestion.annotation_id) == str(annotation_filter)
        ]
        if not matched:
            raise ResourceNotFoundError("标注记录", annotation_filter)
        for suggestion in matched:
            input_tokens = _require_usage_int(item.get("input_tokens"), "input_tokens")
            output_tokens = _require_usage_int(item.get("output_tokens"), "output_tokens")
            # purpose 等合法性由 TokenUsageRecord.__post_init__ 统一校验，
            # 非法值转 DomainValidationError，不在这里复制枚举判断。
            records.append(
                TokenUsageRecord(
                    usage_id=uuid4(),
                    project_id=scope.project_id,
                    model_id=suggestion.model_id,
                    model_version=suggestion.model_version,
                    prompt_version=suggestion.prompt_version,
                    purpose=item.get("purpose") or TokenUsagePurpose.PRE_LABEL,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    occurred_at=item.get("occurred_at") or now,
                    cost_estimate=compute_cost(
                        model_id=suggestion.model_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        price_map=price_map,
                    ),
                    annotation_id=suggestion.annotation_id,
                    created_at=now,
                )
            )
    return tuple(records)


def record_suggestion_usage(
    scope: ProjectScope,
    suggestions: tuple[AiSuggestion, ...],
    *,
    usage: tuple[Mapping[str, object], ...],
    price_map: dict[str, object] | None,
    now: datetime,
    recorder: TokenUsageRecorder | None,
) -> None:
    """登记建议时顺带记账（可选注入；recorder 缺席或无计量条目时不动作）。

    记账与建议同事务（recorder 写 session，UoW 统一提交）：计量失败回滚
    整个登记——成本台账宁可少记（显式失败），不能多记。
    """
    if recorder is None or not usage:
        return
    records = usage_records_from(scope, suggestions, usage=usage, price_map=price_map, now=now)
    if records:
        recorder.record(scope, records)


__all__ = ["TokenUsageRecorder", "record_suggestion_usage", "usage_records_from"]
