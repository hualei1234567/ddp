"""Token 成本台账只读聚合（V52-010，蓝图 N6）。

与 ``dispatch_queue`` 同一模式：一次查询回答"这个项目在 LLM 上花了多少"，
按 ``(model_id, purpose)`` 切分调用次数、tokens 与成本。

**为什么在 SQL 聚合而不是拉全量行回领域层算**：台账是计量事实表，
行数随调用次数线性增长，时间窗聚合（"本月花了多少"）在表变大后
拉全量行不可接受。桶内规则（成本合计只算有价行、``cost_estimated_tokens``
只含有价行的 tokens）由领域层 :class:`~app.domain.annotation.token_usage.CostLedger`
的合计属性承接——本查询只产出条目，"合计怎么算"不在两处实现。

**无价模型的表现**：``cost_estimate`` 为 ``NULL`` 的行不计入成本合计，
但 tokens 与调用次数照常统计；全项目都未配价时 ``total_cost_estimate``
为 ``None``——调用方把它渲染成"无价格数据"，**不是 0**（0 是免费）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Case, func, select
from sqlalchemy.orm import Session

from app.domain.annotation.token_budget import (
    BudgetUsage,
    TokenBudgetEvaluation,
    TokenBudgetPolicy,
    evaluate_token_budget,
)
from app.domain.annotation.token_usage import (
    COST_QUANTUM,
    ModelPurposeLedger,
    TokenUsagePurpose,
)
from app.infrastructure.db.models.annotation_ai_token_usage import AiTokenUsage


@dataclass(frozen=True, slots=True)
class CostLedgerSnapshot:
    """项目级 Token 成本台账快照（可直接序列化）。

    ``total_cost_estimate`` 为 ``None`` 表示全项目无一条有价记录——
    "没有价格数据"与"成本为 0"是两件事。
    """

    project_id: UUID
    entries: tuple[ModelPurposeLedger, ...]
    generated_at: datetime

    @property
    def total_cost_estimate(self) -> Decimal | None:
        """有价部分的成本合计；复用领域口径，不在 SQL 里再写一份。"""
        from app.domain.annotation.token_usage import CostLedger

        ledger = CostLedger(project_id=self.project_id, entries=self.entries)
        return ledger.total_cost_estimate


def read_cost_ledger(
    session: Session,
    *,
    project_id: UUID,
    now: datetime | None = None,
) -> CostLedgerSnapshot:
    """读取项目范围内的 Token 成本台账（只读聚合，不写库）。

    ``project_id`` 由调用方的 owner-scope 提供：跨项目的调用记录绝不
    出现在本项目的成本视图里。
    """
    moment = now or datetime.now(UTC)
    priced_tokens = Case(
        (
            AiTokenUsage.cost_estimate.is_not(None),
            AiTokenUsage.input_tokens + AiTokenUsage.output_tokens,
        ),
        else_=0,
    )
    rows = session.execute(
        select(
            AiTokenUsage.model_id,
            AiTokenUsage.purpose,
            func.count(),
            func.sum(AiTokenUsage.input_tokens),
            func.sum(AiTokenUsage.output_tokens),
            func.sum(AiTokenUsage.cost_estimate),
            func.sum(priced_tokens),
        )
        .where(AiTokenUsage.project_id == project_id)
        .group_by(AiTokenUsage.model_id, AiTokenUsage.purpose)
        .order_by(AiTokenUsage.model_id, AiTokenUsage.purpose)
    ).all()

    entries: list[ModelPurposeLedger] = []
    for (
        model_id,
        purpose,
        call_count,
        input_tokens,
        output_tokens,
        cost_estimate,
        cost_estimated_tokens,
    ) in rows:
        cost = None if cost_estimate is None else Decimal(str(cost_estimate)).quantize(COST_QUANTUM)
        entries.append(
            ModelPurposeLedger(
                model_id=str(model_id),
                purpose=TokenUsagePurpose(str(purpose)),
                call_count=int(call_count),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cost_estimate=cost,
                cost_estimated_tokens=int(cost_estimated_tokens or 0),
            )
        )
    return CostLedgerSnapshot(
        project_id=project_id,
        entries=tuple(entries),
        generated_at=moment,
    )


def usage_from_snapshot(
    snapshot: CostLedgerSnapshot,
    *,
    daily_tokens: int | None = None,
    monthly_tokens: int | None = None,
) -> BudgetUsage:
    """把台账快照换算成预算评估用量（不重复查库、不重复算合计）。

    ``total_tokens`` 取全部条目 tokens 合计；``total_cost_usd`` 复用
    ``CostLedgerSnapshot.total_cost_estimate``（``None`` = 全项目无价记录，
    保持「无数据 ≠ 0」语义）。日/月窗口由调用方按需另行传入——台账快照
    本身不带时间切片，本层不做时间窗推断。
    """
    return BudgetUsage(
        daily_tokens=daily_tokens,
        monthly_tokens=monthly_tokens,
        total_tokens=sum(entry.total_tokens for entry in snapshot.entries),
        monthly_cost_usd=None,
        total_cost_usd=snapshot.total_cost_estimate,
    )


def evaluate_project_token_budget(
    session: Session,
    *,
    policy: TokenBudgetPolicy,
    project_id: UUID,
    now: datetime | None = None,
    usage: BudgetUsage | None = None,
) -> tuple[CostLedgerSnapshot, TokenBudgetEvaluation]:
    """读取项目台账并评估预算，返回（快照, 评估结论）。

    复用 :func:`read_cost_ledger` 的同一次聚合；``usage`` 允许调用方覆盖
    台账无法表达的口径（如按月窗口另查），未提供时由快照推导。
    """
    snapshot = read_cost_ledger(session, project_id=project_id, now=now)
    effective_usage = usage if usage is not None else usage_from_snapshot(snapshot)
    return snapshot, evaluate_token_budget(effective_usage, policy)


__all__ = [
    "CostLedgerSnapshot",
    "evaluate_project_token_budget",
    "read_cost_ledger",
    "usage_from_snapshot",
]
