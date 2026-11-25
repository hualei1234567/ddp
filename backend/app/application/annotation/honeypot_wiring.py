"""黄金题熔断接线（V52-008，蓝图 N3 Honeypot 阶段 2）。

把阶段 1 的纯领域状态机
:class:`~app.domain.annotation.honeypot_breaker.HoneypotBreaker` 接入
真实流程：

1. **提交钩子**：:func:`record_gold_submission` 在黄金题提交评估后调用，
   把命中结果推进持久化状态行（无行 = NORMAL 默认）；
2. **派单闸口**：:class:`HoneypotGatekeeper` 作为可选端口注入派单用例，
   PAUSED → 422 ``annotation_annotator_paused_honeypot``，只挡新分配、
   不收回已有分配；
3. **手动解除**：:func:`resume` 由 owner 角色端点调用，清窗口回 NORMAL。

**开关语义**（``DDP_HONEYPOT_BREAKER_ENABLED``，默认 false）：关闭时
全部入口短路跳过，行为与旧版完全一致——状态行不写、派单不查、
resume 端点幂等返回当前状态（网关不存在即"无需解除"）。

**状态行投影规则**（与领域状态机一一对应）：

- ``window_history`` 忠实保存最近 ``window_size * consecutive_windows``
  条黄金题命中序列（``'0'`` miss / ``'1'`` hit，按提交顺序）——**判定
  必须按顺序分窗，聚合值（hits/total）丢失顺序无法忠实重放**：实测
  反例是"两窗各 80%（真实 PAUSED）"与"窗1 100% + 窗2 60%（真实
  NORMAL）"聚合同为 hits=16/total=20，只存聚合会把前者误判成 NORMAL；
- ``hits`` / ``total`` 是 window_history 的派生聚合列（运维可读 +
  库层约束兜底）；
- 达到连续 2 窗 <85% → 持久化 PAUSED 与原因；未达到时窗口照常滚动；
- PAUSED 后不再接收新命中（领域层会拒绝，应用层先短路）；
- resume 清空窗口回 NORMAL（领域层 ``resume`` 的持久化等价）。

所有判定确定性可复现，无随机数；窗口大小与阈值沿用领域常量
（蓝图 N3：85% / 连续 2 窗），不在应用层另设口径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.annotation.errors import AnnotatorPausedByHoneypotError
from app.domain.annotation.honeypot_breaker import (
    CONSECUTIVE_WINDOWS,
    BreakerState,
    HoneypotBreaker,
)

#: PAUSED 状态下派单被拒的契约错误码
HONEYPOT_PAUSED_CODE = AnnotatorPausedByHoneypotError.code

#: 派单被拒时 details 里的原因码（对齐 V52-009 的 details_rule 口径）
REASON_PAUSED = "HONEYPOT_PAUSED"


class HoneypotGatekeeper(Protocol):
    """派单入口的熔断检查端口（可选注入，对齐 SkillProfileDirectory 先例）。

    实现方从 ``annotation_honeypot_state`` 读当前状态；未注入（``None``）
    或开关关闭时派单行为与旧版完全一致。
    """

    def assert_dispatchable(self, project_id: UUID, annotator_id: UUID) -> None:
        """PAUSED 时抛 :class:`AnnotatorPausedByHoneypotError`，否则静默放行。"""
        ...


def record_gold_submission(
    session: Session,
    project_id: UUID,
    annotator_id: UUID,
    *,
    hit: bool,
    actor_id: UUID,
    enabled: bool,
) -> str:
    """黄金题提交钩子：推进该标注员的持久化熔断状态。

    - 无状态行 = NORMAL 默认（首次提交才建行，零配置项目零写入）；
    - 达到连续 2 窗 <85% → PAUSED（复用 :class:`HoneypotBreaker` 领域逻辑）；
    - PAUSED 后再提交属于异常路径：不改状态，交由派单闸口在源头拦截；
    - ``enabled=False``（开关关闭）直接跳过，行为不变。

    返回写入后的状态值（``NORMAL`` / ``PAUSED``），供调用方留痕。
    """
    if not enabled:
        return BreakerState.NORMAL.value
    from app.infrastructure.db.models.annotation_honeypot_state import (  # noqa: PLC0415
        AnnotationHoneypotState as _Model,
    )

    row = session.scalar(
        select(_Model).where(
            _Model.project_id == project_id,
            _Model.annotator_id == annotator_id,
        )
    )
    if row is not None and row.state == BreakerState.PAUSED.value:
        # PAUSED 期间不应再有新标注；不改行，避免窗口口径被污染。
        return row.state
    breaker = _replay_breaker(row)
    breaker.record_gold_result(hit=hit)
    decision = breaker.evaluate()
    capacity = breaker.window_size * breaker.consecutive_windows
    history = _serialize_history(breaker)
    # 持久化序列 = 判定用的最近滑窗容量条（领域 _hits 超出部分不参与判定）。
    recent = breaker._hits[-capacity:]  # noqa: SLF001
    now = datetime.now(UTC)
    paused_reason = _join_reasons(decision) if decision.is_paused else None
    if row is None:
        session.add(
            _Model(
                id=uuid4(),
                project_id=project_id,
                annotator_id=annotator_id,
                hits=sum(recent),
                total=len(recent),
                window_history=history,
                state=decision.state.value,
                paused_reason=paused_reason,
                paused_at=now if decision.is_paused else None,
                version=1,
                created_at=now,
                updated_at=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    else:
        row.hits = sum(recent)
        row.total = len(recent)
        row.window_history = history
        row.state = decision.state.value
        if decision.is_paused:
            row.paused_reason = paused_reason
            if row.paused_at is None:
                row.paused_at = now
        row.updated_at = now
        row.updated_by = actor_id
    session.flush()
    return decision.state.value


def resume(
    session: Session,
    project_id: UUID,
    annotator_id: UUID,
    *,
    by: UUID,
    enabled: bool,
) -> dict[str, object]:
    """管理员手动解除暂停；清窗口回 NORMAL（幂等：NORMAL 时原样返回）。

    返回解除后的状态快照；开关关闭或本就 NORMAL 时 ``resumed=False``。
    """
    from app.infrastructure.db.models.annotation_honeypot_state import (  # noqa: PLC0415
        AnnotationHoneypotState as _Model,
    )

    if not enabled:
        return {
            "annotator_id": str(annotator_id),
            "state": _current_state(session, project_id, annotator_id),
            "resumed": False,
        }
    row = session.scalar(
        select(_Model).where(
            _Model.project_id == project_id,
            _Model.annotator_id == annotator_id,
        )
    )
    if row is None:
        return {
            "annotator_id": str(annotator_id),
            "state": BreakerState.NORMAL.value,
            "resumed": False,
        }
    if row.state != BreakerState.PAUSED.value:
        return {
            "annotator_id": str(annotator_id),
            "state": row.state,
            "resumed": False,
        }
    now = datetime.now(UTC)
    row.state = BreakerState.NORMAL.value
    row.hits = 0
    row.total = 0
    row.window_history = ""
    row.paused_reason = None
    row.paused_at = None
    row.updated_at = now
    row.updated_by = by
    session.flush()
    return {"annotator_id": str(annotator_id), "state": row.state, "resumed": True}


class SqlHoneypotGatekeeper:
    """:class:`HoneypotGatekeeper` 的 SQLAlchemy 实现（读当前状态行）。"""

    def __init__(self, session: Session, *, enabled: bool) -> None:
        self._session = session
        self._enabled = enabled

    def assert_dispatchable(self, project_id: UUID, annotator_id: UUID) -> None:
        if not self._enabled:
            return
        state, _reason_text = _load_state(self._session, project_id, annotator_id)
        if state == BreakerState.PAUSED.value:
            # details.reason 是机器可读的原因码（契约 details_rule 口径，
            # 对齐 V52-009 的 MISSING_REQUIRED_SKILL）；人类可读的触发依据
            # 在状态行 paused_reason 里，运维查询用，不进 API details。
            raise AnnotatorPausedByHoneypotError(
                annotator_id=annotator_id,
                reason=REASON_PAUSED,
            )


def _load_state(session: Session, project_id: UUID, annotator_id: UUID) -> tuple[str, str | None]:
    from app.infrastructure.db.models.annotation_honeypot_state import (  # noqa: PLC0415
        AnnotationHoneypotState as _Model,
    )

    row = session.scalar(
        select(_Model).where(
            _Model.project_id == project_id,
            _Model.annotator_id == annotator_id,
        )
    )
    if row is None:
        return BreakerState.NORMAL.value, None
    return row.state, row.paused_reason


def _current_state(session: Session, project_id: UUID, annotator_id: UUID) -> str:
    return _load_state(session, project_id, annotator_id)[0]


def _replay_breaker(row: object | None) -> HoneypotBreaker:
    """从持久化行重放领域状态机（window_history → 布尔序列）。

    判定按提交顺序分窗，聚合值丢失顺序无法忠实重放（模块 docstring 的
    实测反例），因此重放只信 ``window_history`` 序列；``hits`` / ``total``
    是派生聚合列，仅作运维可读与库层约束。
    """
    breaker = HoneypotBreaker(
        window_size=WINDOW_SIZE_DEFAULT,
        consecutive_windows=CONSECUTIVE_WINDOWS,
    )
    if row is None:
        return breaker
    history = getattr(row, "window_history", "") or ""
    breaker._hits = [char == "1" for char in history]  # noqa: SLF001
    if getattr(row, "state", None) == BreakerState.PAUSED.value:
        breaker.state = BreakerState.PAUSED
        breaker._paused_reasons = (getattr(row, "paused_reason", None) or "",)  # noqa: SLF001
    return breaker


def _serialize_history(breaker: HoneypotBreaker) -> str:
    """领域窗口序列 → 持久化字符串（与 ``_replay_breaker`` 互逆）。

    只保留最近 ``window_size * consecutive_windows`` 条：领域 ``_hits``
    无上限增长（``evaluate`` 判定时自取最近窗口），持久化层按滑窗容量
    截断——截断对判定无影响（超出部分本就不参与判定）。
    """
    capacity = breaker.window_size * breaker.consecutive_windows
    return "".join("1" if hit else "0" for hit in breaker._hits[-capacity:])  # noqa: SLF001


def _join_reasons(decision: object) -> str:
    reasons = getattr(decision, "reasons", ()) or ()
    return "；".join(reasons)


#: 领域默认窗口大小（蓝图 N3：10 条/窗）。领域模块未导出常量，这里
#: 以同值显式声明并在测试里钉住两者一致，避免 import 私有属性。
WINDOW_SIZE_DEFAULT = 10


__all__ = [
    "AnnotatorPausedByHoneypotError",
    "HONEYPOT_PAUSED_CODE",
    "HoneypotGatekeeper",
    "REASON_PAUSED",
    "SqlHoneypotGatekeeper",
    "WINDOW_SIZE_DEFAULT",
    "record_gold_submission",
    "resume",
]
