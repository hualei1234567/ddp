"""跨数据集评测对比的可比性判定（纯领域，无 IO、确定性）。

规则
----
1. **同数据集版本**内的基线对比永远允许。两次评测喂的是同一批样本，
   数字变化只可能来自模型或阈值——这正是基线对比的原始用途；
2. **跨数据集版本**必须显式声明（``cross_dataset_comparison``）。未声明就比，
   等于让系统替用户假设"两个数据集测的是同一件事"，而这个假设经常不成立，
   且失败方式是**静默给出一个看起来很确定的百分比**；
3. 声明之后仍要校验可比性：两次评测所用模型的 ``task_type`` 必须一致。

判定的边界（如实说明，不要误读成"任务语义完全相同"）
----------------------------------------------------
本仓库里唯一可作任务语义权威落点的字段是 ``Model.task_type``
（``IMITATION`` / ``REINFORCEMENT`` / ``VLM`` / ``OTHER``）：它在模型创建时
确定，且不参与后续更新（``ModelUseCases.update`` 只改 name/description/
framework/tags），因此可以稳定比较。

``Evaluation`` 与 ``DatasetVersion`` 上**没有**任务语义字段。
``Dataset.purpose`` 是用途（训练/评测/基准）不是任务；
``selection_filters`` 是自由 JSON、``annotation_schema_versions`` 只描述标注
结构——两者既非充分也非必要条件，拿来当可比性依据只会产出"看起来严谨、
实则误判"的结论。

因此通过校验只意味着"两次评测的任务大类相同"，**不保证**"测的是同一个技能"。
同为 ``IMITATION`` 时，一个数据集全是 pick、另一个全是 place，仍然会通过。
要把校验推进到技能粒度，需要数据集侧先有任务语义的权威落点（例如 action /
skill 标签进入 ``DatasetVersion`` 的事实快照），那是产品与契约决策，
本模块不假装完成。

取不到任务语义时按不可比处理
----------------------------
**无法判定不等于可比**。缺一侧 ``task_type``（模型行缺失、历史数据不完整）
必须拒绝，而不是默认放行——默认放行的后果正是本模块要消除的那种
"系统一本正经地给出提升 12%"。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.modelops.errors import (
    EvaluationCrossDatasetNotDeclaredError,
    EvaluationNotComparableError,
)

# 不可比的具体原因：作为 ``details.reason`` 回显，客户端据此决定是换基线、
# 换数据集，还是补数据。取值必须是稳定的机器可读字面量（不是中文文案）。
REASON_TASK_TYPE_MISMATCH = "task_type_mismatch"
REASON_TASK_TYPE_UNKNOWN = "task_type_unknown"


@dataclass(frozen=True, slots=True)
class ComparisonSubjects:
    """参与对比的两个数据集版本及其任务语义事实。

    ``*_task_type`` 为 ``None`` 表示**取不到**（不是"无所谓"）：调用方无法
    确定该侧的模型任务类型时需要如实传 ``None``，判定函数会据此拒绝。
    """

    baseline_dataset_version_id: UUID
    current_dataset_version_id: UUID
    baseline_task_type: str | None
    current_task_type: str | None

    @property
    def cross_dataset(self) -> bool:
        return self.baseline_dataset_version_id != self.current_dataset_version_id


def require_comparable(subjects: ComparisonSubjects, *, declared: bool) -> None:
    """校验跨数据集对比是否被允许；不允许时抛领域异常。

    同数据集版本直接返回（既有行为不变）；跨数据集版本依次检查：
    ``declared`` 是否为真、两侧 ``task_type`` 是否都取得到且一致。
    """
    if not subjects.cross_dataset:
        return
    if not declared:
        raise EvaluationCrossDatasetNotDeclaredError(details=_dataset_details(subjects))
    baseline = subjects.baseline_task_type
    current = subjects.current_task_type
    if baseline is None or current is None:
        raise EvaluationNotComparableError(
            details={**_dataset_details(subjects), "reason": REASON_TASK_TYPE_UNKNOWN}
        )
    if baseline != current:
        raise EvaluationNotComparableError(
            details={
                **_dataset_details(subjects),
                "reason": REASON_TASK_TYPE_MISMATCH,
                "baseline_task_type": baseline,
                "current_task_type": current,
            }
        )


def _dataset_details(subjects: ComparisonSubjects) -> dict[str, object]:
    """只含同项目对象标识，不含跨项目存在性信息。"""
    return {
        "baseline_dataset_version_id": str(subjects.baseline_dataset_version_id),
        "current_dataset_version_id": str(subjects.current_dataset_version_id),
    }


__all__ = [
    "REASON_TASK_TYPE_MISMATCH",
    "REASON_TASK_TYPE_UNKNOWN",
    "ComparisonSubjects",
    "require_comparable",
]
