"""运行态汇总（V4.1 FR-042）：一次查询给出投递队列、失败与积压的健康快照。

**为什么不是「又一个 metrics 端点」**

``GET /metrics`` 是 Prometheus 抓取格式，面向时间序列；运维排查时真正需要的是
「现在有没有东西卡住」——一次 JSON 查询就能回答：

- 投递类事件按状态分布（PENDING / PUBLISHED / FAILED）——有没有堆积；
- 重试中的事件（``attempts > 0`` 且仍 PENDING）——Worker 是否在退步；
- 最老一条未处理事件距今多久——判断「积压」还是「完全停了」；
- 按事件类型分组的失败数——哪个下游最先坏。

**为什么不做成"每类对象一个端点"**

Agent / Upload / Worker / QC / Export / Training 的运行态分别散落在各域；
逐个建端点会把运维面切成六个地方看。这里只汇总**跨域共性**：投递队列健康。
各域自身的业务状态仍走各自端点（例如 ``GET /edge-agents`` 看 Agent 在线状态）。

**权限**

运维面数据：只允许项目范围内的成员读取，且不带任何 payload 细节（只给
计数与时间），避免把事件体里的业务字段泄漏成运维可见信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models.collection import OutboxEvent

#: 投递类事件的三个可观测状态。``RECORDED`` 是留痕类终态，不进运维口径。
_DISPATCH_STATUSES = ("PENDING", "PUBLISHED", "FAILED")

#: 积压判定阈值（秒）：超过这个时间的最老未处理事件视为「可能停滞」。
#: 与 Worker 轮询周期相比留足余量，避免正常抖动被误报。
_STALE_THRESHOLD_S = 900


@dataclass(frozen=True, slots=True)
class DispatchQueueSnapshot:
    """投递队列健康快照；所有字段都是可直接展示的标量。"""

    counts: dict[str, int]
    retrying: int
    oldest_pending_age_seconds: int | None
    stale: bool
    failures_by_event_type: tuple[tuple[str, int], ...]
    generated_at: datetime


def read_dispatch_queue(
    session: Session,
    *,
    project_id: object,
    now: datetime | None = None,
) -> DispatchQueueSnapshot:
    """读取项目范围内的投递队列快照（只读，不写库）。

    ``project_id`` 由调用方的 ``ProjectScope`` 提供：跨项目的事件绝不出现在
    本项目运维视图里。
    """
    moment = now or datetime.now(UTC)

    counts: dict[str, int] = {status: 0 for status in _DISPATCH_STATUSES}
    for status, total in session.execute(
        select(OutboxEvent.status, func.count())
        .where(OutboxEvent.project_id == project_id, OutboxEvent.dispatch.is_(True))
        .group_by(OutboxEvent.status)
    ):
        counts[str(status)] = int(total)

    retrying = int(
        session.scalar(
            select(func.count()).where(
                OutboxEvent.project_id == project_id,
                OutboxEvent.dispatch.is_(True),
                OutboxEvent.status == "PENDING",
                OutboxEvent.attempts > 0,
            )
        )
        or 0
    )

    oldest = session.scalar(
        select(func.min(OutboxEvent.available_at)).where(
            OutboxEvent.project_id == project_id,
            OutboxEvent.dispatch.is_(True),
            OutboxEvent.status == "PENDING",
        )
    )
    oldest_age: int | None = None
    if oldest is not None:
        reference = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
        oldest_age = max(0, int((moment - reference).total_seconds()))

    failures: list[tuple[str, int]] = [
        (str(event_type), int(total))
        for event_type, total in session.execute(
            select(OutboxEvent.event_type, func.count())
            .where(
                OutboxEvent.project_id == project_id,
                OutboxEvent.dispatch.is_(True),
                OutboxEvent.status == "FAILED",
            )
            .group_by(OutboxEvent.event_type)
            .order_by(func.count().desc())
        )
    ]

    return DispatchQueueSnapshot(
        counts=counts,
        retrying=retrying,
        oldest_pending_age_seconds=oldest_age,
        stale=oldest_age is not None and oldest_age > _STALE_THRESHOLD_S,
        failures_by_event_type=tuple(failures),
        generated_at=moment,
    )


__all__ = ["DispatchQueueSnapshot", "read_dispatch_queue"]
