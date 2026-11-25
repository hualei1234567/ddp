"""Token 成本台账（V52-010，蓝图 N6「成本经营」）。

## 为什么单独建模

V5.2 阶段 1 的 judge 路由与 PRM 导出已经把 LLM 调用接入预标注与评审，
但**每次调用花了多少 token、折多少钱、换回多少人工工时**没有任何计量：
成本只发生在供应商账单里，平台侧看不到「哪个项目、哪个模型、哪种用途
烧了多少」，N6 的 ROI 对比（Token 成本 vs 节省人工工时）无从谈起。

本模块只做**计量与算式**：

- :class:`TokenUsageRecord`：一次 LLM 调用的计量事实；成本归属键里的
  模型身份三列与 :class:`~app.domain.annotation.ai_suggestion.AiSuggestion`
  同源（常量直接复用，不复制长度上限）；
- :class:`CostLedger`：按 ``(model_id, purpose)`` 汇总调用次数、tokens 与成本；
- :func:`estimate_cost`：按显式传入的价格表折算金额。

## 刻意不做的两件事

- **不算人工工时**：工时节省量由调用方提供（观测或配置给出），本层只提供
  :meth:`CostLedger.roi_compare` 的算式口径。把"每条建议节省几分钟"这类
  业务假设固化进计量层，改口径就得改代码。
- **不猜价格**：``price_map`` 必须显式传入；未配置价格的模型返回
  ``cost_estimate=None``。``None`` 是"没有价格数据"，``0`` 是"免费"，
  两者混用会把漏配当成省钱。

## 精度口径（MEM-INC-008 的同族问题）

金额是货币值，浮点误差会随聚合放大。``cost_estimate`` 在领域内用
:class:`decimal.Decimal` 承载、量化到 4 位小数（0.0001 美元 ≈ 当前主流
模型单次调用成本的最低有效位）；落库用 ``Numeric`` 列，API 对外用字符串
（契约侧 ``type: string``），避免 JSON 双精度丢精度。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.domain.annotation.ai_suggestion import (
    MODEL_ID_MAX_LENGTH,
    MODEL_VERSION_MAX_LENGTH,
    PROMPT_VERSION_MAX_LENGTH,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.validation import (
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    required_uuid,
)

#: 金额量化步长（USD）：0.0001 足以区分主流单价差异且聚合误差可忽略。
COST_QUANTUM: Final[Decimal] = Decimal("0.0001")

#: 价格表单位说明，随估算结果一起展示，防止调用方按错误单位换算。
PRICE_UNIT: Final[str] = "USD per 1k tokens"

_ZERO: Final[Decimal] = Decimal("0")


class TokenUsagePurpose(StrEnum):
    """计量用途；与蓝图 N6 的三类 LLM 调用一一对应。"""

    PRE_LABEL = "PRE_LABEL"
    LLM_JUDGE = "LLM_JUDGE"
    EMBED = "EMBED"


PURPOSES: Final[tuple[TokenUsagePurpose, ...]] = tuple(TokenUsagePurpose)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _decimal_money(value: object, field_name: str) -> Decimal | None:
    """把金额解析成 4 位小数的 ``Decimal``；``None`` 表示"未计价"。"""
    if value is None:
        return None
    number: Decimal | None = None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise DomainValidationError(f"{field_name} 不是合法金额") from exc
    if number is None:
        raise DomainValidationError(f"{field_name} 必须是数字或金额字符串")
    if number < 0:
        raise DomainValidationError(f"{field_name} 不能为负")
    return _quantize(number)


def _price_component(prices: dict[str, object], key: str) -> Decimal | None:
    """取单价并转 ``Decimal``；缺失或不可解析都返回 ``None``（不猜价）。"""
    value = prices.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def compute_cost(
    *,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    price_map: dict[str, object] | None,
) -> Decimal | None:
    """按 ``price_map``（$/1k tokens）计算一次调用成本；无价可算返回 ``None``。

    ``price_map`` 为 ``None``（未配置价格）与"价格为 0"（真免费）语义不同，
    分别映射成 ``None`` 与 ``Decimal("0")``，不互相冒充。单位换算只在
    这里做一次，调用方不要再除 1000——两处各算一半必然漂移。
    """
    if price_map is None:
        return None
    prices = price_map.get(model_id)
    if not isinstance(prices, dict):
        return None
    input_price = _price_component(prices, "input")
    output_price = _price_component(prices, "output")
    if input_price is None or output_price is None:
        return None
    cost = (
        Decimal(input_tokens) / Decimal(1000) * input_price
        + Decimal(output_tokens) / Decimal(1000) * output_price
    )
    return _quantize(cost)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    price_map: dict[str, object] | None,
    *,
    model_id: str,
) -> Decimal | None:
    """估算一次调用成本（USD）。

    位置参数保持任务卡签名 ``(input_tokens, output_tokens, price_map)``；
    ``model_id`` 必须以关键字传入——没有模型名的估算是无归属的数字。

    ``price_map`` 形如 ``{"<model_id>": {"input": 0.002, "output": 0.008}}``，
    单位 **$/1k tokens**。以下情形返回 ``None``（不猜价）：价格表未配置、
    模型不在表里、缺 ``input`` / ``output`` 单价、单价不可解析。
    """
    return compute_cost(
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        price_map=price_map,
    )


def _coerce_purpose(value: object) -> TokenUsagePurpose:
    if isinstance(value, TokenUsagePurpose):
        return value
    try:
        return TokenUsagePurpose(str(value))
    except ValueError as exc:
        allowed = ", ".join(purpose.value for purpose in PURPOSES)
        raise DomainValidationError(f"purpose 必须是 {allowed} 之一") from exc


def _record_text_fields(record: TokenUsageRecord) -> None:
    """校验并规范化模型身份文本列（就地写入 frozen dataclass）。"""
    updates: dict[str, str] = {
        "model_id": normalized_text(record.model_id, "model_id", max_length=MODEL_ID_MAX_LENGTH),
        "model_version": normalized_text(
            record.model_version, "model_version", max_length=MODEL_VERSION_MAX_LENGTH
        ),
    }
    if record.prompt_version is not None:
        updates["prompt_version"] = normalized_text(
            record.prompt_version,
            "prompt_version",
            max_length=PROMPT_VERSION_MAX_LENGTH,
        )
    for name, value in updates.items():
        object.__setattr__(record, name, value)


@dataclass(frozen=True, slots=True)
class TokenUsageRecord:
    """一次 LLM 调用的计量事实。

    成本归属键 ``(project_id, model_id, model_version, prompt_version, purpose)``
    与 :class:`~app.domain.annotation.ai_suggestion.AiSuggestion` 的模型身份
    三列同源——成本要能对回"哪个模型版本、哪套 Prompt 花的钱"。

    不变量（构造即校验，库层 CHECK 兜底）：

    - ``input_tokens`` / ``output_tokens`` ≥ 0（0 合法：缓存命中可无输出）；
    - ``cost_estimate`` ≥ 0，``None`` 表示未配置价格；
    - ``purpose`` 必须是 :class:`TokenUsagePurpose` 合法值；
    - ``occurred_at`` 必须 timezone-aware（与全库时间语义一致）。
    """

    usage_id: UUID
    project_id: UUID
    model_id: str
    model_version: str
    purpose: TokenUsagePurpose
    input_tokens: int
    output_tokens: int
    occurred_at: datetime
    cost_estimate: Decimal | None = None
    prompt_version: str | None = None
    annotation_id: UUID | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        required_uuid(self.usage_id, "usage_id")
        required_uuid(self.project_id, "project_id")
        _record_text_fields(self)
        object.__setattr__(self, "purpose", _coerce_purpose(self.purpose))
        object.__setattr__(
            self, "input_tokens", non_negative_int(self.input_tokens, "input_tokens")
        )
        object.__setattr__(
            self,
            "output_tokens",
            non_negative_int(self.output_tokens, "output_tokens"),
        )
        object.__setattr__(
            self, "cost_estimate", _decimal_money(self.cost_estimate, "cost_estimate")
        )
        if self.annotation_id is not None:
            object.__setattr__(
                self, "annotation_id", required_uuid(self.annotation_id, "annotation_id")
            )
        if self.occurred_at is None:
            raise DomainValidationError("occurred_at 不能为空")
        object.__setattr__(
            self,
            "occurred_at",
            optional_aware_datetime(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(
            self, "created_at", optional_aware_datetime(self.created_at, "created_at")
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_payload(self) -> dict[str, object]:
        """对外输出形态：金额用字符串（契约 ``type: string``，MEM-INC-008 同族）。"""
        cost = self.cost_estimate
        return {
            "usage_id": str(self.usage_id),
            "project_id": str(self.project_id),
            "model_id": self.model_id,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "purpose": self.purpose.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_estimate": None if cost is None else str(cost),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ModelPurposeLedger:
    """单个 ``(model_id, purpose)`` 桶的累计计量。

    ``cost_estimated_tokens`` 是**有价可算**的 token 数：无价模型（成本为
    ``None``）的 tokens 不进这个数，避免"漏配价格"被读成"这部分没花成本"。
    """

    model_id: str
    purpose: TokenUsagePurpose
    call_count: int
    input_tokens: int
    output_tokens: int
    cost_estimate: Decimal | None
    cost_estimated_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_payload(self) -> dict[str, object]:
        cost = self.cost_estimate
        return {
            "model_id": self.model_id,
            "purpose": self.purpose.value,
            "call_count": self.call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_estimate": None if cost is None else str(cost),
            "cost_estimated_tokens": self.cost_estimated_tokens,
        }


@dataclass(frozen=True, slots=True)
class RoiCompare:
    """Token 成本与节省人工工时的 ROI 对比结果。

    口径（蓝图 N6）：``saved_labor_hours`` 由调用方提供——本层不算工时；
    ``hourly_rate`` 是人工审核时薪（货币/小时）。全部金额用字符串输出。

    - ``saved_labor_cost = saved_labor_hours * hourly_rate``；
    - ``token_cost`` 是台账里有价可算部分的合计（``None`` 表示全部无价）；
    - ``roi = saved_labor_cost / token_cost``：>1 表示省的工时价值超过
      Token 花费；``token_cost`` 为 0 或无价时返回 ``None``——除零不是
      无穷大，"没有成本数据"也不是"成本为零"。
    """

    token_cost: Decimal | None
    saved_labor_hours: float
    hourly_rate: float
    saved_labor_cost: Decimal

    @property
    def roi(self) -> Decimal | None:
        if self.token_cost is None or self.token_cost == 0:
            return None
        return _quantize(self.saved_labor_cost / self.token_cost)

    def to_payload(self) -> dict[str, object]:
        cost = self.token_cost
        roi = self.roi
        return {
            "token_cost": None if cost is None else str(cost),
            "saved_labor_hours": self.saved_labor_hours,
            "hourly_rate": str(Decimal(str(self.hourly_rate))),
            "saved_labor_cost": str(self.saved_labor_cost),
            "roi": None if roi is None else str(roi),
        }


@dataclass(frozen=True, slots=True)
class CostLedger:
    """按 ``(model_id, purpose)`` 聚合的成本台账。

    ``entries`` 按键排序（确定性）；``project_id`` 只用于对回项目，不参与
    聚合键。全量合计见 :attr:`total_cost_estimate`。
    """

    project_id: UUID
    entries: tuple[ModelPurposeLedger, ...]

    @property
    def total_cost_estimate(self) -> Decimal | None:
        """有价可算部分的成本合计；全部无价时为 ``None``（不是 0）。"""
        costs = [entry.cost_estimate for entry in self.entries if entry.cost_estimate is not None]
        if not costs:
            return None
        return _quantize(sum(costs, _ZERO))

    def roi_compare(
        self,
        *,
        human_review_hours_saved: float,
        hourly_rate: float,
    ) -> RoiCompare:
        """与节省人工工时做 ROI 对比（只做算式，工时量由调用方提供）。

        ``human_review_hours_saved``：调用方按自己的口径给出的节省小时数
        （观测统计或配置估算）；本层不推导。时薪与工时相乘在 Decimal 域
        完成，避免浮点乘法把货币值算脏。
        """
        if human_review_hours_saved < 0 or hourly_rate < 0:
            raise DomainValidationError("节省工时与时薪不能为负")
        saved_cost = _quantize(Decimal(str(human_review_hours_saved)) * Decimal(str(hourly_rate)))
        return RoiCompare(
            token_cost=self.total_cost_estimate,
            saved_labor_hours=human_review_hours_saved,
            hourly_rate=hourly_rate,
            saved_labor_cost=saved_cost,
        )


def _ledger_entry(
    model_id: str,
    purpose: TokenUsagePurpose,
    items: list[TokenUsageRecord],
) -> ModelPurposeLedger:
    """聚合同一个 ``(model_id, purpose)`` 桶。"""
    priced = [item for item in items if item.cost_estimate is not None]
    cost = _quantize(sum((item.cost_estimate for item in priced), _ZERO)) if priced else None
    return ModelPurposeLedger(
        model_id=model_id,
        purpose=purpose,
        call_count=len(items),
        input_tokens=sum(item.input_tokens for item in items),
        output_tokens=sum(item.output_tokens for item in items),
        cost_estimate=cost,
        cost_estimated_tokens=sum(item.total_tokens for item in priced),
    )


def aggregate_usage(records: object) -> CostLedger:
    """把计量记录按 ``(model_id, purpose)`` 聚合成台账。

    输入必须全部属于同一项目（成本台账不跨项目混合）。空输入非法——
    "没有调用"由调用方用空列表之外的分支表达，静默返回空台账会把
    "忘记传数据"掩盖成"本月零成本"。
    """
    buckets: dict[tuple[str, TokenUsagePurpose], list[TokenUsageRecord]] = {}
    project_ids: set[UUID] = set()
    for record in records:  # type: ignore[attr-defined]
        if not isinstance(record, TokenUsageRecord):
            raise DomainValidationError("聚合输入必须是 TokenUsageRecord")
        project_ids.add(record.project_id)
        buckets.setdefault((record.model_id, record.purpose), []).append(record)
    if len(project_ids) != 1:
        raise DomainValidationError("成本台账必须限定在单个项目内聚合且非空")
    project_id = next(iter(project_ids))
    entries = tuple(
        _ledger_entry(model_id, purpose, items)
        for (model_id, purpose), items in sorted(buckets.items())
    )
    return CostLedger(project_id=project_id, entries=entries)


__all__ = [
    "COST_QUANTUM",
    "PRICE_UNIT",
    "PURPOSES",
    "CostLedger",
    "ModelPurposeLedger",
    "RoiCompare",
    "TokenUsagePurpose",
    "TokenUsageRecord",
    "aggregate_usage",
    "compute_cost",
    "estimate_cost",
]
