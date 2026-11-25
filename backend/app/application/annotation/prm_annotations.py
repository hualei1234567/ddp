"""PRM 步级标注用例：登记、查询、分叉重写预览（V52-020）。

## 边界

- **登记**（``create``）：轨迹标注整体创建（含部分标注），创建即不可变——
  步级标注是复核对象而非编辑对象；修正走新轨迹行，不原地覆盖。
  调用方必须是项目写权限持有者（``WriteScope`` 已在 API 层强制）。
- **查询**（``get`` / ``get_by_trajectory`` / ``list_for_project``）：只读，
  项目隔离由 ``ProjectScope`` 强制。
- **分叉重写**（``fork_preview``）：**纯计算端点**——给定点位与重写序列，
  返回分叉轨迹与 Step-DPO 对预览，不落库。调用方确认后再用 ``create``
  以新 trajectory_id 物化（``ForkedTrajectory.to_prm_annotation``）。
  先例：负载预览（capacity preview）同为只读计算端点。

偏好对以 JSON 约定值形态回流提交链（MEM-DEC-002），本用例不写
``annotation_records``，只产出对的数据结构。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.ports import (
    AuditRepository,
    OutboxRepository,
    PrmAnnotationRepository,
    PrmAnnotationView,
    UnitOfWork,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
)
from app.domain.annotation.prm_dpo import StepDpoPair, to_step_dpo_pair
from app.domain.annotation.prm_step import ForkedTrajectory, PrmAnnotation, fork_from_step
from app.domain.identity.models import ProjectScope

# 事件是留痕类（dispatch=False）：没有后台动作消费它，去掉后系统行为不变。
PRM_ANNOTATION_RECORDED_EVENT = "annotation.prm_annotation.recorded"

_RESOURCE_TYPE = "PRM_ANNOTATION"


class PrmAnnotationUseCases:
    def __init__(
        self,
        prm_annotations: PrmAnnotationRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._prm_annotations = prm_annotations
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    # ------------------------------------------------------------------ 登记
    def create(
        self,
        scope: ProjectScope,
        annotation: PrmAnnotation,
        *,
        request_id: str | None = None,
    ) -> PrmAnnotationView:
        """登记一条轨迹的步级标注；trajectory 重复视为业务键冲突。"""
        if self._prm_annotations.get_by_trajectory(scope, annotation.trajectory_id) is not None:
            raise DuplicateResourceError(f"轨迹 {annotation.trajectory_id} 已存在步级标注")
        annotation_id = uuid4()
        view = self._prm_annotations.add(
            scope,
            annotation,
            annotation_id=annotation_id,
            actor_id=scope.actor_id,
        )
        self._record_event(
            scope,
            annotation,
            annotation_id=annotation_id,
            action="record",
            event_type=PRM_ANNOTATION_RECORDED_EVENT,
            request_id=request_id,
        )
        self._uow.commit()
        return view

    def create_forked(
        self,
        scope: ProjectScope,
        forked: ForkedTrajectory,
        *,
        forked_trajectory_id: str,
        request_id: str | None = None,
    ) -> PrmAnnotationView:
        """把分叉轨迹物化成独立标注行（与原轨迹构成 Step-DPO 对的 chosen 侧）。"""
        materialized = forked.to_prm_annotation(forked_trajectory_id)
        return self.create(scope, materialized, request_id=request_id)

    # ------------------------------------------------------------------ 查询
    def get(self, scope: ProjectScope, annotation_id: UUID) -> PrmAnnotationView:
        view = self._prm_annotations.get(scope, annotation_id)
        if view is None:
            raise ResourceNotFoundError("PRM 步级标注", annotation_id)
        return view

    def get_by_trajectory(self, scope: ProjectScope, trajectory_id: str) -> PrmAnnotationView:
        view = self._prm_annotations.get_by_trajectory(scope, trajectory_id)
        if view is None:
            raise ResourceNotFoundError("PRM 步级标注", trajectory_id)
        return view

    def list_for_project(
        self,
        scope: ProjectScope,
        *,
        limit: int = 50,
    ) -> list[PrmAnnotationView]:
        if limit < 1 or limit > 200:
            raise DomainValidationError("limit 必须在 1 到 200 之间")
        return self._prm_annotations.list_for_project(scope, limit=limit)

    # ------------------------------------------------------------------ 分叉
    def fork_preview(
        self,
        scope: ProjectScope,
        trajectory_id: str,
        *,
        at_index: int,
        new_actions: tuple[str, ...],
    ) -> tuple[ForkedTrajectory, StepDpoPair, PrmAnnotation]:
        """分叉重写预览（纯计算，不落库）：返回分叉轨迹、Step-DPO 对与物化实体。

        Step-DPO 对语义：分叉点前共享前缀（进 prompt）；分叉点后
        chosen = Forked 重写序列、rejected = Original 原序列。
        """
        prm_view = self.get_by_trajectory(scope, trajectory_id)
        prm = prm_view.annotation
        forked = fork_from_step(prm, at_index, *new_actions)
        pair = to_step_dpo_pair(prm, forked)
        return forked, pair, forked.to_prm_annotation(f"{trajectory_id}#fork-preview")

    # ------------------------------------------------------------------ 内部
    def _record_event(
        self,
        scope: ProjectScope,
        annotation: PrmAnnotation,
        *,
        annotation_id: UUID,
        action: str,
        event_type: str,
        request_id: str | None,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=annotation_id,
            action=action,
            from_status=None,
            to_status="RECORDED",
            reason=None,
            request_id=request_id,
            details={
                "trajectory_id": annotation.trajectory_id,
                "step_count": annotation.step_count,
                "marked_count": annotation.labeled_step_count,
            },
            event_type=event_type,
            event_payload={
                "trajectory_id": annotation.trajectory_id,
                "step_count": annotation.step_count,
                "marked_count": annotation.labeled_step_count,
            },
            occurred_at=datetime.now(UTC),
        )


__all__ = [
    "PRM_ANNOTATION_RECORDED_EVENT",
    "PrmAnnotationUseCases",
]
