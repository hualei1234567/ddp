"""评测失败案例回流（V52-030，蓝图 N5 数据飞轮入口 + G5）。

## 为什么单独建模

V5.2 阶段 1/2 已经把 LLM Judge、PRM 步级标注接进标注平台，但**评测平台的
失败案例没有任何回流通道**：评测发现模型在哪类能力（capability）上失败，
这些 bad case 只停留在评测报告里，无法自动变成人工标注任务，数据飞轮的
「失败 → 标注 → 再训练」入口缺失（蓝图 G5）。

本模块只做**回流判定**：

- :class:`BackflowCase`：一条评测失败案例的领域事实（source 系统身份、
  case_ref 外部唯一键、capability 维度、失败摘要、payload JSON）；
- :class:`BackflowDecision`：一条案例进入平台时的判定结果
  （CREATE_TASK / DUPLICATE_SKIPPED / RATE_LIMITED）+ 原因；
- :func:`decide_incoming`：批量判定的纯函数——去重与限流都在这里，
  API/应用层只消费结论，不重复实现规则。

## 幂等核心（MEM-INC-001 的同族问题）

同一 ``(source, case_ref)`` 的案例可能被评测平台**重发**（网络重试、
回调风暴）。去重的权威判定有三层：

1. **批次内去重**（纯函数 :func:`decide_incoming` 首层）：同一批次里重复
   出现的 ``(source, case_ref)`` 只受理一次；
2. **批次间查询**：应用层判定前按唯一键批量查既有行（``existing_refs``），
   查到 → DUPLICATE_SKIPPED；
3. **数据库唯一约束兜底**：并发双写时后到者撞 ``uq_backflow_source_ref``
   的 ``IntegrityError``，仓储**必须捕获并翻译成 DUPLICATE_SKIPPED**——
   绝不让 IntegrityError 冒泡成「可重试失败」无限重试（MEM-INC-001：
   漏捕获导致事件永久 FAILED 的教训）。

三层语义一致：重复案例永远是幂等成功（DUPLICATE_SKIPPED），不是错误。

## 限流（保护标注队列）

单 source 在统计窗口内已受理数 + 本批数超过 N（默认 200，部署配置
``DDP_BACKFLOW_HOURLY_LIMIT``）时，该 source 的后续案例按 RATE_LIMITED
处理：**拒绝但保留判定上下文**，其余案例照常受理——单个 source 的流量
洪峰不拖垮整批接收，也不冲垮标注队列。已受理基数由应用层查询注入
（``accepted_in_window``），本函数只做合并判定，不猜窗口。

## 优先级映射

capability 维度 + 失败次数 → 既有 :class:`TaskPriority`（P0–P3），
:func:`map_priority` 是唯一权威实现；调用方也可显式传 ``suggested_priority``
覆盖（评测平台自带建议时尊重它，但仍受枚举校验约束）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

from app.domain.annotation.enums import TaskPriority
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.validation import (
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
)

#: 单 source 每小时受理上限（部署配置 ``DDP_BACKFLOW_HOURLY_LIMIT`` 可覆盖）。
DEFAULT_HOURLY_LIMIT: Final[int] = 200

#: 限流统计窗口（固定 1 小时；领域常量，不随配置变化）。
RATE_WINDOW: Final[timedelta] = timedelta(hours=1)

#: 单批次受理上限：评测平台一次回调的案例数上限（与 0061 失败用例 200 条
#: 上限同口径；超大批次应分批回调，而不是单请求打满）。
MAX_BATCH_SIZE: Final[int] = 200

#: capability 维度白名单：评测平台按这些维度报告能力失败。
#: 未知维度不拒绝（评测平台可能先于平台演进新维度），按 P2 保守受理。
KNOWN_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "TOOL_USE",
        "PLANNING",
        "PERCEPTION",
        "NAVIGATION",
        "INSTRUCTION_FOLLOWING",
        "LONG_HORIZON",
        "MANIPULATION",
    }
)

#: 高风险维度：直接关系机器人动作正确性，失败回流的基础优先级更高。
HIGH_RISK_CAPABILITIES: Final[frozenset[str]] = frozenset({"TOOL_USE", "PLANNING", "NAVIGATION"})

#: 失败次数达到该值视为系统性缺陷，优先级升一级。
HIGH_FAILURE_COUNT: Final[int] = 50

#: source 系统标识上限（如 "eval-platform-prod"）。
SOURCE_MAX_LENGTH: Final[int] = 64

#: 外部案例键上限（评测平台的案例 ID / hash）。
CASE_REF_MAX_LENGTH: Final[int] = 200

#: capability 维度文本上限。
CAPABILITY_MAX_LENGTH: Final[int] = 64

#: 失败摘要 / 判定原因文本上限。
SUMMARY_MAX_LENGTH: Final[int] = 1000

_PRIORITY_UP: Final[dict[TaskPriority, TaskPriority]] = {
    TaskPriority.P3: TaskPriority.P2,
    TaskPriority.P2: TaskPriority.P1,
    TaskPriority.P1: TaskPriority.P0,
    TaskPriority.P0: TaskPriority.P0,
}


class BackflowStatus(StrEnum):
    """回流案例状态。

    RECEIVED 是受理态（待转换）；CONVERTED 是成功创建任务后的终态；
    SKIPPED_DUPLICATE / RATE_LIMITED 是**受理时刻的判定记录**——它们
    记录"这次回调发生了什么"，不是案例自身的生命周期。
    """

    RECEIVED = "RECEIVED"
    CONVERTED = "CONVERTED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    RATE_LIMITED = "RATE_LIMITED"


STATUS_ALLOWED_SQL: Final[str] = "('RECEIVED', 'CONVERTED', 'SKIPPED_DUPLICATE', 'RATE_LIMITED')"


class BackflowDecisionKind(StrEnum):
    """单条案例的回流判定。"""

    CREATE_TASK = "CREATE_TASK"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
    RATE_LIMITED = "RATE_LIMITED"


def map_priority(capability: str, failure_count: int) -> TaskPriority:
    """capability 维度 + 失败次数 → 任务优先级（唯一权威实现）。

    规则（显式、可解释，禁止黑盒）：

    - 高风险维度（``HIGH_RISK_CAPABILITIES``）基础 P1；其余已知维度基础 P2；
    - 失败次数 ≥ ``HIGH_FAILURE_COUNT`` 升一级（P2→P1、P1→P0）：同一维度
      反复失败意味着系统性缺陷，早处理止损；
    - 白名单外的未知维度固定 P2：优先级必须保守，禁止对未知语义自动升级。

    ``failure_count`` 必须 ≥1：0 次失败不构成回流事实。
    """
    if not isinstance(capability, str) or not capability.strip():
        raise DomainValidationError("capability 不能为空")
    count = positive_int(failure_count, "failure_count")
    if capability in HIGH_RISK_CAPABILITIES:
        base = TaskPriority.P1
    else:
        base = TaskPriority.P2
    if count >= HIGH_FAILURE_COUNT:
        base = _PRIORITY_UP[base]
    return base


def _coerce_status(value: BackflowStatus | str) -> BackflowStatus:
    if isinstance(value, BackflowStatus):
        return value
    try:
        return BackflowStatus(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in BackflowStatus)
        raise DomainValidationError(f"status 必须是 {allowed} 之一") from exc


def _coerce_priority(value: TaskPriority | str) -> TaskPriority:
    if isinstance(value, TaskPriority):
        return value
    try:
        return TaskPriority(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in TaskPriority)
        raise DomainValidationError(f"priority 必须是 {allowed} 之一") from exc


@dataclass(frozen=True, slots=True)
class BackflowCase:
    """一条评测失败案例（回流受理的领域事实）。

    不变量（构造即校验，库层 CHECK 兜底）：

    - ``source`` / ``case_ref`` 非空——两者构成幂等键 ``(source, case_ref)``；
    - ``capability`` 非空（回流必带维度，否则优先级映射无从谈起）；
    - ``payload`` 是自由 JSON（评测平台的原始报告片段），平台只存储不解释；
    - ``received_at`` 必须 timezone-aware（与全库时间语义一致）；
    - ``failure_count`` ≥ 1。
    """

    case_id: UUID
    project_id: UUID
    source: str
    case_ref: str
    capability: str
    payload: Mapping[str, object]
    received_at: datetime
    status: BackflowStatus | str = BackflowStatus.RECEIVED
    failure_summary: str | None = None
    failure_count: int = 1
    suggested_priority: TaskPriority | str = TaskPriority.P2
    task_id: UUID | None = None
    decision_reason: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        required_uuid(self.case_id, "case_id")
        required_uuid(self.project_id, "project_id")
        object.__setattr__(
            self,
            "source",
            normalized_text(self.source, "source", max_length=SOURCE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "case_ref",
            normalized_text(self.case_ref, "case_ref", max_length=CASE_REF_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "capability",
            normalized_text(self.capability, "capability", max_length=CAPABILITY_MAX_LENGTH),
        )
        object.__setattr__(self, "payload", frozen_mapping(self.payload, "payload"))
        object.__setattr__(
            self,
            "failure_summary",
            optional_text(self.failure_summary, "failure_summary", max_length=SUMMARY_MAX_LENGTH),
        )
        object.__setattr__(self, "failure_count", positive_int(self.failure_count, "failure_count"))
        object.__setattr__(self, "status", _coerce_status(self.status))
        object.__setattr__(self, "suggested_priority", _coerce_priority(self.suggested_priority))
        object.__setattr__(self, "task_id", optional_uuid(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "decision_reason",
            optional_text(self.decision_reason, "decision_reason", max_length=SUMMARY_MAX_LENGTH),
        )
        received = optional_aware_datetime(self.received_at, "received_at")
        if received is None:
            raise DomainValidationError("received_at 不能为空")
        object.__setattr__(self, "received_at", received)
        object.__setattr__(
            self, "created_at", optional_aware_datetime(self.created_at, "created_at")
        )

    @property
    def dedup_key(self) -> tuple[str, str]:
        """幂等键 ``(source, case_ref)``；与库层唯一约束同一语义。"""
        return (self.source, self.case_ref)

    def to_payload(self) -> dict[str, object]:
        """对外输出形态（API 响应直接消费）。"""
        return {
            "case_id": str(self.case_id),
            "project_id": str(self.project_id),
            "source": self.source,
            "case_ref": self.case_ref,
            "capability": self.capability,
            "payload": dict(self.payload),
            "received_at": self.received_at.isoformat(),
            "status": self.status.value,
            "failure_summary": self.failure_summary,
            "failure_count": self.failure_count,
            "suggested_priority": self.suggested_priority.value,
            "task_id": None if self.task_id is None else str(self.task_id),
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, slots=True)
class BackflowDecision:
    """一条案例的回流判定（结论 + 可解释原因）。

    ``decision`` 为 ``CREATE_TASK`` 时 ``case`` 是待入库的新案例；
    ``DUPLICATE_SKIPPED`` / ``RATE_LIMITED`` 时 ``case`` 携带判定上下文
    （重复键或限流窗口），是否落库留痕由应用层决定。
    """

    decision: BackflowDecisionKind
    case: BackflowCase
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason", normalized_text(self.reason, "reason", max_length=SUMMARY_MAX_LENGTH)
        )


@dataclass(frozen=True, slots=True)
class BackflowDecisionBatch:
    """一批回调的判定汇总（API 响应直接消费）。"""

    decisions: tuple[BackflowDecision, ...]
    accepted: int
    duplicated: int
    rate_limited: int

    def __post_init__(self) -> None:
        non_negative_int(self.accepted, "accepted")
        non_negative_int(self.duplicated, "duplicated")
        non_negative_int(self.rate_limited, "rate_limited")
        total = self.accepted + self.duplicated + self.rate_limited
        if total != len(self.decisions):
            raise DomainValidationError("判定计数与 decisions 数量不一致")


def parse_case(
    raw: Mapping[str, object],
    *,
    project_id: UUID,
    received_at: datetime,
    case_id: UUID | None = None,
) -> BackflowCase:
    """把回调载荷的单条案例转成领域实体（缺关键字段即校验失败）。

    ``case_id`` 缺省由本函数生成（uuid4）：仓储落库用它当主键，
    **禁止**用批次序号等确定性值伪造——两次不同请求的第 1 条案例会撞主键。
    """
    source = raw.get("source")
    case_ref = raw.get("case_ref")
    capability = raw.get("capability")
    if source is None or case_ref is None or capability is None:
        raise DomainValidationError("案例缺少 source / case_ref / capability")
    payload_raw = raw.get("payload") or {}
    if not isinstance(payload_raw, Mapping):
        raise DomainValidationError("payload 必须是 JSON 对象")
    failure_count_raw = raw.get("failure_count", 1)
    failure_count = max(1, non_negative_int(failure_count_raw, "failure_count"))
    priority_raw = raw.get("suggested_priority")
    if priority_raw is None:
        priority: TaskPriority = map_priority(str(capability), failure_count)
    else:
        priority = _coerce_priority(str(priority_raw))
    return BackflowCase(
        case_id=case_id if case_id is not None else uuid4(),
        project_id=project_id,
        source=str(source),
        case_ref=str(case_ref),
        capability=str(capability),
        payload=dict(payload_raw),
        received_at=received_at,
        failure_summary=optional_text(
            raw.get("failure_summary"), "failure_summary", max_length=SUMMARY_MAX_LENGTH
        ),
        failure_count=failure_count,
        suggested_priority=priority,
    )


def classify_batch(
    cases: tuple[Mapping[str, object], ...],
    existing_refs: frozenset[tuple[str, str]],
    hourly_count: Mapping[str, int],
    *,
    project_id: UUID,
    received_at: datetime,
    limit: int = DEFAULT_HOURLY_LIMIT,
    case_id_factory: Callable[[], UUID] = uuid4,
) -> tuple[BackflowDecisionBatch, tuple[BackflowCase, ...]]:
    """批量回流判定的纯函数（去重 + 限流 + 优先级映射的唯一入口）。

    规则（按顺序短路，每条案例恰好得到一个判定）：

    1. 批次内重复 ``(source, case_ref)``：只有第一条受理，后续
       DUPLICATE_SKIPPED（评测平台同一批次内重发时幂等成功）；
    2. 与库内既有记录重复（``existing_refs``，应用层按项目预先批量查询的
       **已受理**键集）：DUPLICATE_SKIPPED；
    3. 该 source 统计窗口内已受理数（``hourly_count``）+ 本批已受理数
       达到 ``limit``：RATE_LIMITED——超出部分拒绝但保留判定上下文，
       其余案例照常处理（限流只影响该 source 自己）；
    4. 其余 → CREATE_TASK，优先级由 :func:`map_priority` 映射。

    返回 ``(判定批次, 待受理案例列表)``：只有 CREATE_TASK 的案例进入第二个
    返回值；``case_id_factory`` 供测试注入确定性 ID（生产用 uuid4）。
    ``received_at`` 必须 timezone-aware；``existing_refs`` 与 ``hourly_count``
    由应用层在判定前一次性批量查询，不在循环里逐条 SELECT。
    """
    if len(cases) > MAX_BATCH_SIZE:
        raise DomainValidationError(f"单批次不得超过 {MAX_BATCH_SIZE} 条案例")
    moment = optional_aware_datetime(received_at, "received_at")
    if moment is None:
        raise DomainValidationError("received_at 不能为空")
    capped_limit = positive_int(limit, "limit")
    window_base = {
        source: non_negative_int(count, "hourly_count") for source, count in hourly_count.items()
    }

    accepted: list[BackflowCase] = []
    decisions: list[BackflowDecision] = []
    seen_in_batch: set[tuple[str, str]] = set()
    accepted_this_batch: dict[str, int] = {}

    for raw in cases:
        case = parse_case(raw, project_id=project_id, received_at=moment)
        case_id = case_id_factory()
        case = replace(case, case_id=case_id)
        key = case.dedup_key
        if key in seen_in_batch or key in existing_refs:
            decisions.append(
                BackflowDecision(
                    decision=BackflowDecisionKind.DUPLICATE_SKIPPED,
                    case=case,
                    reason="重复的 (source, case_ref)：案例已受理过，幂等跳过",
                )
            )
            continue
        if (
            window_base.get(case.source, 0) + accepted_this_batch.get(case.source, 0)
            >= capped_limit
        ):
            decisions.append(
                BackflowDecision(
                    decision=BackflowDecisionKind.RATE_LIMITED,
                    case=case,
                    reason=(
                        f"source={case.source} 本窗口受理数已达上限 {capped_limit}，"
                        "超出部分拒绝（可随下一窗口重试）"
                    ),
                )
            )
            continue
        accepted.append(case)
        seen_in_batch.add(key)
        accepted_this_batch[case.source] = accepted_this_batch.get(case.source, 0) + 1
        decisions.append(
            BackflowDecision(
                decision=BackflowDecisionKind.CREATE_TASK,
                case=case,
                reason=(
                    f"受理：capability={case.capability}，"
                    f"建议优先级 {case.suggested_priority.value}"
                ),
            )
        )

    batch = BackflowDecisionBatch(
        decisions=tuple(decisions),
        accepted=len(accepted),
        duplicated=sum(
            1 for item in decisions if item.decision is BackflowDecisionKind.DUPLICATE_SKIPPED
        ),
        rate_limited=sum(
            1 for item in decisions if item.decision is BackflowDecisionKind.RATE_LIMITED
        ),
    )
    return batch, tuple(accepted)


__all__ = [
    "BackflowCase",
    "BackflowDecision",
    "BackflowDecisionBatch",
    "BackflowDecisionKind",
    "BackflowStatus",
    "CASE_REF_MAX_LENGTH",
    "CAPABILITY_MAX_LENGTH",
    "DEFAULT_HOURLY_LIMIT",
    "HIGH_FAILURE_COUNT",
    "HIGH_RISK_CAPABILITIES",
    "KNOWN_CAPABILITIES",
    "MAX_BATCH_SIZE",
    "RATE_WINDOW",
    "SOURCE_MAX_LENGTH",
    "STATUS_ALLOWED_SQL",
    "SUMMARY_MAX_LENGTH",
    "classify_batch",
    "map_priority",
    "parse_case",
]
