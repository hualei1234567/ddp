"""质检结论驱动路由（V31-032）：结论 → 标注提交动作序列的唯一映射。

**为什么放在领域层**：``QUALITY_RUN_TRANSITIONS`` 与 ``QualityRunStatus`` 都在
本域，而"哪种结论该触发哪种下游动作"是同一份业务规则的另一半。把它写进应用
层会让"结论有哪些值"与"每个值该做什么"分处两地，新增结论时必然漏改。

**为什么是序列而不是单动作**：标注提交的状态机不允许跳步——``SUBMITTED``
不能直接到 ``REWORK_REQUIRED``，必须经 ``IN_REVIEW`` 与 ``REJECTED``。
把"质检失败"硬压成一条边会绕过状态机，也会让审计里出现一条不存在的迁移。
因此这里返回**按顺序执行的动作序列**，每一步都由既有状态机裁决。

**只映射 ``SUCCEEDED`` 运行的结论**：``lifecycle`` 未到 ``SUCCEEDED`` 时
``status`` 为空（ADR-0042），此时没有任何可信结论可路由。执行失败
（``lifecycle = FAILED``）同理——失败的是执行，不是数据。

**不路由的对象类型显式返回空序列**，而不是"默认什么都不做"：调用方需要
区分"没有路由规则"与"路由规则存在但结论不需要动作"。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.annotation.enums import AnnotationSubmissionAction
from app.domain.quality.enums import (
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySubjectType,
)

#: 质检结论 → 标注提交动作序列（按顺序执行）。
#:
#: - ``PASSED`` → 进入人工复核（``SUBMITTED → IN_REVIEW``）；
#: - ``FAILED`` → 进入人工复核后由系统驳回并要求返工
#:   （``SUBMITTED → IN_REVIEW → REJECTED → REWORK_REQUIRED``）。这不是
#:   "自动替人做决定"：质检不通过本身就是驳回理由，复核环节保留在链路里，
#:   审计能看到完整的迁移序列；
#: - ``INCOMPLETE`` **不映射**：既没通过也没失败，推进任何状态都是伪造结论。
_ROUTE_TABLE: dict[QualityRunStatus, tuple[AnnotationSubmissionAction, ...]] = {
    QualityRunStatus.PASSED: (AnnotationSubmissionAction.SYSTEM_REVIEW_START,),
    QualityRunStatus.FAILED: (
        AnnotationSubmissionAction.SYSTEM_REVIEW_START,
        AnnotationSubmissionAction.REJECT,
        AnnotationSubmissionAction.REQUIRE_REWORK,
    ),
}

#: 结论路由只适用于这些对象类型。``DATASOURCE_IMPORT`` 的结论决定是否物化
#: Episode，与标注提交无关；``DATASET_VERSION`` 的结论进入 Dataset 门禁，
#: 同样不改标注提交。
_ROUTABLE_SUBJECTS: frozenset[QualitySubjectType] = frozenset({QualitySubjectType.EPISODE})


@dataclass(frozen=True, slots=True)
class QualityRoutingDecision:
    """一次路由判定的结果。

    ``actions`` 为空表示"这条结论不需要任何动作"，``reason`` 说明原因，便于
    调用方记录与排障。``reason`` 不是错误信息——不需要动作是正常结果。
    """

    actions: tuple[AnnotationSubmissionAction, ...]
    reason: str

    @property
    def should_route(self) -> bool:
        return bool(self.actions)

    @property
    def action_labels(self) -> tuple[str, ...]:
        """动作的字符串形式，供结果对象与日志使用。"""
        return tuple(action.value for action in self.actions)


def decide_route(
    *,
    lifecycle: QualityRunLifecycle | str,
    status: QualityRunStatus | str | None,
    subject_type: QualitySubjectType | str,
) -> QualityRoutingDecision:
    """由运行的生命周期、结论与对象类型决定下游动作序列。

    判定顺序固定为「生命周期 → 对象类型 → 结论」，因为三者中任何一步不满足
    都不该继续往下看：未完成的运行没有结论，不可路由的对象类型无论结论如何
    都不改标注提交。
    """
    resolved_lifecycle = QualityRunLifecycle(lifecycle)
    if resolved_lifecycle is not QualityRunLifecycle.SUCCEEDED:
        return QualityRoutingDecision(
            actions=(),
            reason=f"运行尚未产出结论（lifecycle={resolved_lifecycle.value}），不路由",
        )

    resolved_subject = QualitySubjectType(subject_type)
    if resolved_subject not in _ROUTABLE_SUBJECTS:
        return QualityRoutingDecision(
            actions=(),
            reason=f"对象类型 {resolved_subject.value} 不驱动标注提交，不路由",
        )

    if status is None:
        # 领域与数据库都保证 SUCCEEDED 必有 status；走到这里说明数据被绕过
        # 领域层写入。宁可显式拒绝，也不猜一个动作。
        return QualityRoutingDecision(
            actions=(),
            reason="SUCCEEDED 运行缺少结论，拒绝猜测下游动作",
        )

    resolved_status = QualityRunStatus(status)
    actions = _ROUTE_TABLE.get(resolved_status, ())
    if not actions:
        return QualityRoutingDecision(
            actions=(),
            reason=f"结论 {resolved_status.value} 不触发标注动作",
        )
    return QualityRoutingDecision(
        actions=actions,
        reason=f"结论 {resolved_status.value} → {' → '.join(a.value for a in actions)}",
    )


__all__ = ["QualityRoutingDecision", "decide_route"]
