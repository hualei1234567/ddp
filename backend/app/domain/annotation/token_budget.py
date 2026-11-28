"""Token 预算与告警评估（V61-019，蓝图 N6「成本经营」）。

## 为什么单独建模

V5.2 已有 ``CostLedger`` 只读台账（"花了多少"），但没有任何「花超了怎么办」：
预算上限、预警阈值、超限判定与告警语义全部缺失，成本失控时平台侧无信号。

本模块补上**判定层**：

- :class:`TokenBudgetPolicy`：显式的日 / 月 / 项目级预算（Token 数与 USD 金额），
  ``warning_ratio`` 决定何时从 ``OK`` 升到 ``WARNING``；
- :func:`evaluate_token_budget`：纯函数，对给定用量与策略产出确定性
  :class:`TokenBudgetStatus`（含逐维度评估明细）。

## 刻意不做的三件事

1. **不读数据库**：用量由调用方（应用层用例）从台账聚合后传入，本层只判定；
2. **不推告警**：只产出状态与超限维度，投递由既有告警通道负责——
   "判定"与"投递"混做会让重试语义与告警去重纠缠不清；
3. **不猜阈值**：策略必须显式传入；未配置预算的维度返回 ``UNLIMITED``，
   不是 ``OK``——「没有设预算」与「预算内」是两件事。

## 语义口径

- ``limit`` 为 ``None``：该维度未设预算 → ``LIMITED=False``；
- ``usage`` 为 ``None``（如全项目无价记录）：成本维度按 ``UNKNOWN`` 处理，
  **不当作 0**，也不触发告警（无数据 ≠ 超支）；
- 任一维度 ``EXCEEDED`` → 整体 ``EXCEEDED``；否则任一 ``WARNING`` → 整体
  ``WARNING``；否则 ``OK``。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.token_usage import COST_QUANTUM

_DEFAULT_WARNING_RATIO: Final[float] = 0.80
_RATIO_QUANTUM: Final[Decimal] = Decimal("0.0001")


class BudgetDimension(StrEnum):
    """预算维度的稳定标识（对外输出与告警关联键）。"""

    DAILY_TOKENS = "DAILY_TOKENS"
    MONTHLY_TOKENS = "MONTHLY_TOKENS"
    TOTAL_TOKENS = "TOTAL_TOKENS"
    MONTHLY_COST = "MONTHLY_COST"
    TOTAL_COST = "TOTAL_COST"


class BudgetVerdict(StrEnum):
    """单维度判定结论。"""

    UNLIMITED = "UNLIMITED"  # 未设预算
    OK = "OK"  # 用量 < warning_ratio * limit
    WARNING = "WARNING"  # warning_ratio * limit <= 用量 <= limit
    EXCEEDED = "EXCEEDED"  # 用量 > limit
    UNKNOWN = "UNKNOWN"  # 用量未知（如全项目无价记录），不判超限


class TokenBudgetStatus(StrEnum):
    """整体预算结论。"""

    OK = "OK"
    WARNING = "WARNING"
    EXCEEDED = "EXCEEDED"
    UNLIMITED = "UNLIMITED"  # 所有维度均未设预算


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """一次评估的用量快照；``None`` 表示该口径无数据（不是 0）。"""

    daily_tokens: int | None = None
    monthly_tokens: int | None = None
    total_tokens: int | None = None
    monthly_cost_usd: Decimal | None = None
    total_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("daily_tokens", "monthly_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(f"{name} 必须是非负整数或 None")
        for name in ("monthly_cost_usd", "total_cost_usd"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, Decimal):
                raise DomainValidationError(f"{name} 必须是 Decimal 或 None")
            if value < 0:
                raise DomainValidationError(f"{name} 不能为负")


@dataclass(frozen=True, slots=True)
class TokenBudgetPolicy:
    """显式预算策略；所有上限均为可选（``None`` = 该维度不设预算）。"""

    daily_tokens: int | None = None
    monthly_tokens: int | None = None
    total_tokens: int | None = None
    monthly_cost_usd: Decimal | None = None
    total_cost_usd: Decimal | None = None
    warning_ratio: float = _DEFAULT_WARNING_RATIO

    def __post_init__(self) -> None:
        for name in ("daily_tokens", "monthly_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise DomainValidationError(f"{name} 必须是正整数或 None")
        for name in ("monthly_cost_usd", "total_cost_usd"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, Decimal):
                raise DomainValidationError(f"{name} 必须是 Decimal 或 None")
            if value <= 0:
                raise DomainValidationError(f"{name} 必须为正数或 None")
        ratio = self.warning_ratio
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, int | float)
            or not 0 < float(ratio) <= 1
        ):
            raise DomainValidationError("warning_ratio 必须在 (0, 1] 之间")

    @property
    def is_configured(self) -> bool:
        """至少设置了一个维度才算「配了预算」。"""
        return any(
            value is not None
            for value in (
                self.daily_tokens,
                self.monthly_tokens,
                self.total_tokens,
                self.monthly_cost_usd,
                self.total_cost_usd,
            )
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "daily_tokens": self.daily_tokens,
            "monthly_tokens": self.monthly_tokens,
            "total_tokens": self.total_tokens,
            "monthly_cost_usd": (
                None if self.monthly_cost_usd is None else str(self.monthly_cost_usd)
            ),
            "total_cost_usd": None if self.total_cost_usd is None else str(self.total_cost_usd),
            "warning_ratio": self.warning_ratio,
        }


@dataclass(frozen=True, slots=True)
class BudgetDimensionResult:
    """单维度判定明细（可直接序列化与入告警 payload）。"""

    dimension: BudgetDimension
    verdict: BudgetVerdict
    usage: str | None
    limit: str | None
    ratio: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "verdict": self.verdict.value,
            "usage": self.usage,
            "limit": self.limit,
            "ratio": self.ratio,
        }


@dataclass(frozen=True, slots=True)
class TokenBudgetEvaluation:
    """整体预算评估结论。"""

    status: TokenBudgetStatus
    policy: TokenBudgetPolicy
    dimensions: tuple[BudgetDimensionResult, ...]

    @property
    def breached_dimensions(self) -> tuple[BudgetDimension, ...]:
        """``EXCEEDED`` 的维度（告警正文与去重键来源）。"""
        return tuple(
            item.dimension for item in self.dimensions if item.verdict is BudgetVerdict.EXCEEDED
        )

    @property
    def warning_dimensions(self) -> tuple[BudgetDimension, ...]:
        return tuple(
            item.dimension for item in self.dimensions if item.verdict is BudgetVerdict.WARNING
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "policy": self.policy.to_payload(),
            "dimensions": [item.to_payload() for item in self.dimensions],
            "breached_dimensions": [item.value for item in self.breached_dimensions],
            "warning_dimensions": [item.value for item in self.warning_dimensions],
        }


def _ratio(usage: Decimal, limit: Decimal) -> Decimal:
    return (usage / limit).quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def _judge(
    dimension: BudgetDimension,
    usage: Decimal | None,
    limit: Decimal | None,
    warning_ratio: Decimal,
) -> BudgetDimensionResult:
    if limit is None:
        return BudgetDimensionResult(
            dimension=dimension,
            verdict=BudgetVerdict.UNLIMITED,
            usage=None if usage is None else str(usage),
            limit=None,
            ratio=None,
        )
    if usage is None:
        # 无数据 ≠ 0：不判超限，也不假装在预算内。
        return BudgetDimensionResult(
            dimension=dimension,
            verdict=BudgetVerdict.UNKNOWN,
            usage=None,
            limit=str(limit),
            ratio=None,
        )
    ratio = _ratio(usage, limit)
    if usage > limit:
        verdict = BudgetVerdict.EXCEEDED
    elif ratio >= warning_ratio:
        verdict = BudgetVerdict.WARNING
    else:
        verdict = BudgetVerdict.OK
    return BudgetDimensionResult(
        dimension=dimension,
        verdict=verdict,
        usage=str(usage),
        limit=str(limit),
        ratio=str(ratio),
    )


def evaluate_token_budget(
    usage: BudgetUsage,
    policy: TokenBudgetPolicy,
) -> TokenBudgetEvaluation:
    """对用量快照与预算策略做确定性判定（纯函数）。"""
    warning_ratio = Decimal(str(policy.warning_ratio))

    def _dec(value: int | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    def _cost(value: Decimal | None) -> Decimal | None:
        return None if value is None else value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)

    dimensions = (
        _judge(
            BudgetDimension.DAILY_TOKENS,
            _dec(usage.daily_tokens),
            _dec(policy.daily_tokens),
            warning_ratio,
        ),
        _judge(
            BudgetDimension.MONTHLY_TOKENS,
            _dec(usage.monthly_tokens),
            _dec(policy.monthly_tokens),
            warning_ratio,
        ),
        _judge(
            BudgetDimension.TOTAL_TOKENS,
            _dec(usage.total_tokens),
            _dec(policy.total_tokens),
            warning_ratio,
        ),
        _judge(
            BudgetDimension.MONTHLY_COST,
            _cost(usage.monthly_cost_usd),
            _cost(policy.monthly_cost_usd),
            warning_ratio,
        ),
        _judge(
            BudgetDimension.TOTAL_COST,
            _cost(usage.total_cost_usd),
            _cost(policy.total_cost_usd),
            warning_ratio,
        ),
    )

    verdicts = {item.verdict for item in dimensions}
    if BudgetVerdict.EXCEEDED in verdicts:
        status = TokenBudgetStatus.EXCEEDED
    elif BudgetVerdict.WARNING in verdicts:
        status = TokenBudgetStatus.WARNING
    elif verdicts == {BudgetVerdict.UNLIMITED}:
        status = TokenBudgetStatus.UNLIMITED
    else:
        status = TokenBudgetStatus.OK
    return TokenBudgetEvaluation(status=status, policy=policy, dimensions=dimensions)


__all__ = [
    "BudgetDimension",
    "BudgetDimensionResult",
    "BudgetUsage",
    "BudgetVerdict",
    "TokenBudgetEvaluation",
    "TokenBudgetPolicy",
    "TokenBudgetStatus",
    "evaluate_token_budget",
]
