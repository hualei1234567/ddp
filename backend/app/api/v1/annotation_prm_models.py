"""PRM 步级标注 API 请求/响应模型（V52-020/V52-032）。

字段口径与领域层 ``prm_step`` 一致：marks 可部分标注（未标 ≠ 正确）、
fork 端点默认是纯计算预览（不落库），``materialize=true`` 时同请求落库并
返回 ``materialized_annotation_id``。错误复用标注域既有错误码，无新增。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.annotation.prm_dpo import StepDpoPair
from app.domain.annotation.prm_step import PrmAnnotation, StepMark


class StepMarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=32)
    comment: str | None = Field(default=None, max_length=2000)
    annotator: str | None = Field(default=None, max_length=128)


class StepMarkOut(BaseModel):
    step_index: int
    label: str
    reward: float
    comment: str | None = None
    annotator: str | None = None
    marked_at: datetime | None = None

    @classmethod
    def from_domain(cls, mark: StepMark) -> StepMarkOut:
        return cls(
            step_index=mark.step_index,
            label=mark.label.value,
            reward=mark.reward,
            comment=mark.comment,
            annotator=mark.annotator,
            marked_at=mark.marked_at,
        )


class PrmAnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trajectory_id: str = Field(min_length=1, max_length=200)
    step_actions: tuple[str, ...] = Field(min_length=1, max_length=512)
    marks: tuple[StepMarkIn, ...] = ()
    task_goal: str | None = Field(default=None, max_length=2000)


class PrmAnnotationOut(BaseModel):
    annotation_id: UUID
    trajectory_id: str
    task_goal: str | None
    step_actions: list[str]
    marks: list[StepMarkOut]
    step_count: int
    marked_count: int
    fork_lineage: dict[str, object] | None = None
    total_reward: float | None = None
    label_counts: dict[str, int] = {}
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_domain(
        cls,
        annotation: PrmAnnotation,
        annotation_id: UUID,
        version: int,
        *,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> PrmAnnotationOut:
        lineage = annotation.fork_lineage
        return cls(
            annotation_id=annotation_id,
            trajectory_id=annotation.trajectory_id,
            task_goal=annotation.task_goal,
            step_actions=list(annotation.step_actions),
            marks=[StepMarkOut.from_domain(mark) for mark in annotation.marks],
            step_count=annotation.step_count,
            marked_count=annotation.labeled_step_count,
            fork_lineage=dict(lineage) if lineage is not None else None,
            total_reward=annotation.total_reward(),
            label_counts=annotation.label_counts(),
            version=version,
            created_at=created_at,
            updated_at=updated_at,
        )


class PrmForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_index: int = Field(ge=0)
    new_actions: tuple[str, ...] = Field(min_length=1, max_length=512)
    #: 默认 false = 纯计算预览（旧客户端零影响）；true = 同请求内分叉 + 落库，
    #: 需要项目写权限（预览只读即可）。
    materialize: bool = False


class PrmForkPreviewOut(BaseModel):
    """分叉重写结果：分叉轨迹 + Step-DPO 对；``materialize=true`` 时含落库 ID。"""

    source_trajectory_id: str
    fork_point_index: int
    forked_trajectory_id_preview: str
    forked_steps: list[str]
    shared_prefix_steps: int
    forked_marks: list[StepMarkOut]
    dpo_pair: StepDpoPairOut
    #: 物化成功后的新标注 ID；纯预览时为 null。
    materialized_annotation_id: UUID | None = None


class StepDpoPairOut(BaseModel):
    """Step-DPO 对（canonical 语义）：chosen=Forked、rejected=Original。"""

    source_trajectory_id: str
    fork_point_index: int
    prompt: str
    chosen: str
    rejected: str
    rationale: str | None = None
    criteria_scores: dict[str, float] = {}
    preference_json_value: dict[str, object]

    @classmethod
    def from_domain(cls, pair: StepDpoPair) -> StepDpoPairOut:
        return cls(
            source_trajectory_id=pair.source_trajectory_id,
            fork_point_index=pair.fork_point_index,
            prompt=pair.prompt,
            chosen=pair.chosen,
            rejected=pair.rejected,
            rationale=pair.rationale,
            criteria_scores=dict(pair.criteria_scores),
            preference_json_value=pair.to_payload(),
        )
