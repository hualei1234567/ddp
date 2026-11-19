"""候选缺口用例（N7-005）：只读聚合 + 显式人工转需求。

职责边界
--------
- ``list``：把当前项目近期评测的失败事实交给纯函数 ``build_gap_candidates``
  聚合成候选视图。既有维度键取自 N7-004 的缺口视图（反馈 + 实测覆盖），
  因此候选只可能挂到平台已经确认的维度上。
- ``convert``：**显式人工动作**。服务端重新计算当前候选，找到目标候选后调用
  既有 ``RequirementUseCases.create`` 创建 **DRAFT** 需求；维度、来源评测与
  ``source_type`` 全部由服务端推导。本用例不调用任何 ``approve`` / ``submit``
  动作，也没有定时任务或事件回调入口，因此不存在自动创建或自动审批路径。

并发与幂等
----------
候选是**实时计算**的只读视图，不落表：转换时以候选键重新定位，候选已消失或
无法归因时拒绝（404 / 422），绝不凭客户端提交的维度建需求。重复请求由调用方
传入的 ``Idempotency-Key`` 复用既有需求创建幂等语义。
"""

from __future__ import annotations

from app.application.modelops.commands import NewRequirement, NewRequirementFromCandidate
from app.application.modelops.feedback import FeedbackUseCases
from app.application.modelops.ports import GapCandidateDirectory
from app.application.modelops.requirements import RequirementUseCases
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import RequirementSourceType
from app.domain.modelops.errors import DomainValidationError, ResourceNotFoundError
from app.domain.modelops.gap_candidates import (
    MAX_CANDIDATE_LIMIT,
    GapCandidateSummary,
    build_gap_candidates,
)
from app.domain.modelops.gaps import GapDimension
from app.domain.modelops.models import DataRequirement


class GapCandidateUseCases:
    def __init__(
        self,
        *,
        candidates: GapCandidateDirectory,
        feedback: FeedbackUseCases,
        requirements: RequirementUseCases,
    ) -> None:
        self._candidates = candidates
        self._feedback = feedback
        self._requirements = requirements

    def list(self, scope: ProjectScope, *, limit: int = 50) -> GapCandidateSummary:
        """候选缺口只读视图；不写库、不创建需求。"""
        scan = self._candidates.failure_case_scan(scope)
        # 复用 N7-004 的缺口视图作为"权威维度键"来源：反馈人工维度 + 实测覆盖。
        # 候选人永远不能凭失败提示值造出新维度。
        known_keys = [
            (item.dimension_type, item.dimension_value)
            for item in self._feedback.gap_summary(scope).items
        ]
        return build_gap_candidates(
            scan.facts,
            known_keys=known_keys,
            limit=limit,
            scan_truncated=scan.truncated,
            evaluations_scanned=scan.evaluations_scanned,
            invalid_evaluation_count=scan.invalid_evaluation_count,
        )

    def convert(
        self,
        scope: ProjectScope,
        candidate_key: str,
        command: NewRequirementFromCandidate,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataRequirement:
        """人工确认后从候选创建 DRAFT 需求。

        只接受已归因到既有维度键的候选；无法归因或候选已消失时拒绝，避免把
        ``suggested_gap`` 提示值当成权威维度。创建走既有需求用例，状态固定为
        DRAFT，**不自动提交或审批**。
        """
        summary = self.list(scope, limit=MAX_CANDIDATE_LIMIT)
        candidate = next(
            (item for item in summary.items if item.candidate_key == candidate_key), None
        )
        if candidate is None:
            if any(item.candidate_key == candidate_key for item in summary.unmapped):
                raise DomainValidationError(
                    "候选缺口无法归因到既有维度，不能自动转为数据需求；"
                    "请先通过反馈或实测建立维度，或人工创建数据需求"
                )
            raise ResourceNotFoundError("候选缺口", candidate_key)

        requirement_command = NewRequirement(
            code=command.code,
            title=command.title,
            # 来源固定为评测失败事实；不接受客户端指定，避免来源失真。
            source_type=RequirementSourceType.EVALUATION.value,
            dimensions=[
                GapDimension(
                    dimension_type=candidate.dimension_type,
                    dimension_value=candidate.dimension_value,
                )
            ],
            owner_id=command.owner_id,
            target_episode_count=command.target_episode_count,
            description=command.description,
            source_feedback_ids=None,
            source_evaluation_ids=list(candidate.source_evaluation_ids),
            quality_thresholds=command.quality_thresholds,
            priority=command.priority,
            due_at=command.due_at,
        )
        return self._requirements.create(
            scope,
            requirement_command,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )


__all__ = ["GapCandidateUseCases"]
