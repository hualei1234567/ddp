"""质检结论驱动路由用例（V31-032）。

职责边界：

- **判定**在领域层（``domain.quality.routing.decide_route``），本模块不写
  "哪种结论对应哪种动作"；
- 本模块只做编排：读结论 → 找目标提交 → 调用**既有**提交用例 → 把
  "状态机拒绝"翻译成幂等成功；
- 不直接改表、不新写状态机。

**为什么路由失败不回滚质检运行**：质检结论是已经发生的事实，路由是它的下游
动作。路由失败（目标提交被并发推进）只记录告警并返回 ``PARTIAL``——把运行
改成 ``FAILED`` 会让"算出来的结论"消失，运维也无从重放。

**幂等靠目标状态机**：同一结论被重复消费时，提交已处于目标状态，状态机会
拒绝这次迁移。这与 ``reviews.py`` 里双人复核 claim 的处理一致：不推进不是
错误，先到者已经完成了这次迁移。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.annotation.ports import AnnotationRecordRepository, MemberDirectory
from app.application.annotation.submissions import AnnotationSubmissionUseCases
from app.application.quality.ports import QualityRunRepository
from app.domain.annotation.enums import (
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
)
from app.domain.annotation.errors import (
    AnnotationDomainError,
    InvalidStateTransitionError,
)
from app.domain.identity.models import ProjectRole, ProjectScope
from app.domain.quality.errors import ResourceNotFoundError
from app.domain.quality.routing import decide_route

logger = logging.getLogger("ddp.quality.routing")

#: 只有这些状态的提交会被路由推进。``DRAFT`` 还没提交（结论会过期），
#: 终态（``APPROVED`` / ``REWORK_REQUIRED`` / ``SUPERSEDED`` …）不该回退。
_ROUTABLE_STATUSES = frozenset({AnnotationSubmissionStatus.SUBMITTED.value})


class QualityRoutingStatus:
    """单次路由的终态（字符串常量，便于日志与测试直接比较）。"""

    ROUTED = "ROUTED"
    SKIPPED = "SKIPPED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class QualityRoutingResult:
    status: str
    run_id: UUID
    actions: tuple[str, ...] = ()
    affected_submission_ids: tuple[UUID, ...] = ()
    reason: str = ""


class QualityRoutingUseCases:
    def __init__(
        self,
        runs: QualityRunRepository,
        records: AnnotationRecordRepository,
        submissions: AnnotationSubmissionUseCases,
        members: MemberDirectory,
    ) -> None:
        self._runs = runs
        self._records = records
        self._submissions = submissions
        self._members = members

    def route(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        now: datetime | None = None,
    ) -> QualityRoutingResult:
        """把一次已产出结论的运行路由到标注提交。

        运行不存在或跨项目时抛 :class:`ResourceNotFoundError`——路由没有
        "空操作"语义，读不到就是读不到。
        """
        run = self._runs.find_any(scope, run_id)
        if run is None:
            raise ResourceNotFoundError("质量运行", run_id)

        decision = decide_route(
            lifecycle=run.lifecycle,
            status=run.status,
            subject_type=run.subject_type,
        )
        if not decision.should_route:
            return QualityRoutingResult(
                status=QualityRoutingStatus.SKIPPED,
                run_id=run_id,
                reason=decision.reason,
            )

        moment = now or datetime.now(UTC)
        submission_ids = self._submitted_submissions(scope, run.subject_id)
        if not submission_ids:
            return QualityRoutingResult(
                status=QualityRoutingStatus.SKIPPED,
                run_id=run_id,
                actions=decision.action_labels,
                reason=f"{decision.reason}；该 Episode 下没有可推进的 SUBMITTED 提交",
            )

        # 路由以项目负责人名义执行：``require-rework`` 明确禁止标注人本人
        # 发起（职责分离），自动路由若沿用入队人身份会被权限校验拒绝。
        # 与 ``consistency._auto_approve`` 同一做法。
        actor_id = self._routing_actor(scope)
        if actor_id is None:
            return QualityRoutingResult(
                status=QualityRoutingStatus.SKIPPED,
                run_id=run_id,
                actions=decision.action_labels,
                reason=f"{decision.reason}；项目内没有可作为自动路由人的负责人",
            )
        routing_scope = ProjectScope(project_id=scope.project_id, actor_id=actor_id)

        applied: list[UUID] = []
        failed: list[UUID] = []
        for submission_id in submission_ids:
            try:
                self._apply(routing_scope, submission_id, decision.actions, moment)
            except InvalidStateTransitionError:
                # 幂等：提交已被推进到目标状态（或更后面的状态）。
                applied.append(submission_id)
            except AnnotationDomainError as exc:
                failed.append(submission_id)
                logger.warning(
                    "质检结论路由失败 run_id=%s submission_id=%s error=%s",
                    run_id,
                    submission_id,
                    exc,
                )
            else:
                applied.append(submission_id)

        status = QualityRoutingStatus.ROUTED if not failed else QualityRoutingStatus.PARTIAL
        return QualityRoutingResult(
            status=status,
            run_id=run_id,
            actions=decision.action_labels,
            affected_submission_ids=tuple(applied),
            reason=decision.reason,
        )

    def _routing_actor(self, scope: ProjectScope) -> UUID | None:
        """挑一个项目负责人作为自动路由的操作者。

        优先排除入队人本人（若入队人恰好是负责人，仍可用——负责人对任意
        提交都有复核权限）。没有负责人时返回 ``None``，由调用方如实报告
        "没有人能路由"，而不是伪造一个操作者。
        """
        owners = self._members.list_member_ids(scope, role=ProjectRole.OWNER.value)
        if not owners:
            return None
        preferred = [owner for owner in owners if owner != scope.actor_id]
        return (preferred or owners)[0]

    def _submitted_submissions(
        self,
        scope: ProjectScope,
        episode_id: UUID,
    ) -> tuple[UUID, ...]:
        """该 Episode 下处于 ``SUBMITTED`` 的提交（去重、保持稳定顺序）。

        通过标注记录反查提交：记录是 Episode 与提交之间的权威关联，路由不
        另建一张"哪个提交属于哪个 Episode"的表。
        """
        # 只取 ``SUBMITTED`` 记录：``DRAFT`` 还没提交（结论会过期），
        # 已通过/已驳回的记录对应终态提交，回退它们会破坏历史。
        records = self._records.list_for_episode(
            scope,
            episode_id,
            statuses=frozenset({AnnotationSubmissionStatus.SUBMITTED.value}),
        )
        seen: dict[UUID, None] = {}
        for record in records:
            seen.setdefault(record.submission_id, None)
        return tuple(seen)

    def _apply(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        actions: tuple[AnnotationSubmissionAction, ...],
        moment: datetime,
    ) -> None:
        """按序列逐步推进；每一步都重新读取版本号（CAS 要求最新值）。"""
        submission = self._submissions.get(scope, submission_id)
        if submission.status.value not in _ROUTABLE_STATUSES:
            # 目标不在可路由状态：状态机本来也会拒绝，这里提前给出精确原因。
            raise InvalidStateTransitionError("标注提交", submission.status.value, actions[0].value)
        for action in actions:
            current = self._submissions.get(scope, submission_id)
            self._submissions.apply_action(
                scope,
                submission_id,
                action.value,
                expected_version=current.version,
                reason=f"质检结论自动路由：{action.value}",
                method="POST",
                path=f"/annotation-submissions/{submission_id}/actions/{action.value}",
            )


__all__ = [
    "QualityRoutingResult",
    "QualityRoutingStatus",
    "QualityRoutingUseCases",
]
