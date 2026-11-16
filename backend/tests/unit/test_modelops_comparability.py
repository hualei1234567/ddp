"""跨数据集版本基线对比：可比性判定（纯领域，无 IO）。

这里验证的是**规则本身**。数据从哪来、HTTP 怎么返回由 API 用例覆盖。

为什么这些用例重要：跨数据集对比一旦被无条件放开，系统会在两次评测任务语义
不同（一个测 pick、一个测 place）时照样算出"提升 12%"，且没有任何报错。
这种错误比"不让比"危险得多，因为看起来像结论。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.modelops.comparability import (
    REASON_TASK_TYPE_MISMATCH,
    REASON_TASK_TYPE_UNKNOWN,
    ComparisonSubjects,
    require_comparable,
)
from app.domain.modelops.errors import (
    DomainValidationError,
    EvaluationCrossDatasetNotDeclaredError,
    EvaluationNotComparableError,
)
from app.domain.modelops.models import Evaluation


def _subjects(
    *,
    same_dataset: bool = False,
    baseline_task_type: str | None = "IMITATION",
    current_task_type: str | None = "IMITATION",
) -> ComparisonSubjects:
    baseline_version = uuid4()
    current_version = baseline_version if same_dataset else uuid4()
    return ComparisonSubjects(
        baseline_dataset_version_id=baseline_version,
        current_dataset_version_id=current_version,
        baseline_task_type=baseline_task_type,
        current_task_type=current_task_type,
    )


def test_same_dataset_version_allowed_without_declaration() -> None:
    """既有行为不变：同数据集版本对比不需要声明，也不看任务类型。"""
    require_comparable(_subjects(same_dataset=True), declared=False)
    # 同数据集时即使任务类型取不到也不该被拒——两次喂的是同一批样本。
    require_comparable(
        _subjects(same_dataset=True, baseline_task_type=None, current_task_type=None),
        declared=False,
    )


def test_cross_dataset_without_declaration_is_rejected() -> None:
    with pytest.raises(EvaluationCrossDatasetNotDeclaredError) as exc_info:
        require_comparable(_subjects(), declared=False)
    assert exc_info.value.code == "evaluation_cross_dataset_not_declared"
    details = exc_info.value.details or {}
    assert set(details) == {"baseline_dataset_version_id", "current_dataset_version_id"}


def test_cross_dataset_declared_and_task_types_match_is_allowed() -> None:
    require_comparable(_subjects(), declared=True)


def test_cross_dataset_declared_but_task_types_differ_is_rejected() -> None:
    with pytest.raises(EvaluationNotComparableError) as exc_info:
        require_comparable(
            _subjects(baseline_task_type="IMITATION", current_task_type="VLM"), declared=True
        )
    assert exc_info.value.code == "evaluation_not_comparable"
    details = exc_info.value.details or {}
    assert details["reason"] == REASON_TASK_TYPE_MISMATCH
    assert details["baseline_task_type"] == "IMITATION"
    assert details["current_task_type"] == "VLM"


@pytest.mark.parametrize(
    ("baseline_task_type", "current_task_type"),
    [(None, "IMITATION"), ("IMITATION", None), (None, None)],
)
def test_unknown_task_type_is_not_treated_as_comparable(
    baseline_task_type: str | None, current_task_type: str | None
) -> None:
    """无法判定可比 **不得** 当作可比放行。

    这是本模块的核心防线：把 None 折叠成"一致"等于用"没检查"冒充"检查通过"。
    """
    with pytest.raises(EvaluationNotComparableError) as exc_info:
        require_comparable(
            _subjects(baseline_task_type=baseline_task_type, current_task_type=current_task_type),
            declared=True,
        )
    assert (exc_info.value.details or {})["reason"] == REASON_TASK_TYPE_UNKNOWN


def test_cross_dataset_flag_does_not_affect_same_dataset_path() -> None:
    """同数据集时 declared 取值无关紧要：两条路径不能互相影响。"""
    require_comparable(_subjects(same_dataset=True), declared=True)
    require_comparable(_subjects(same_dataset=True), declared=False)


# --------------------------------------------------------------------------- #
# 领域实体：基线 ID 与基线数据集版本必须成对出现
# --------------------------------------------------------------------------- #
def _evaluation_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "evaluation_id": uuid4(),
        "project_id": uuid4(),
        "model_version_id": uuid4(),
        "dataset_version_id": uuid4(),
        "thresholds": {"success_rate": {"min": 0.9}},
    }
    kwargs.update(overrides)
    return kwargs


def test_evaluation_allows_no_baseline() -> None:
    """无基线的评测合法：两个字段都为 None。"""
    evaluation = Evaluation(**_evaluation_kwargs())
    assert evaluation.baseline_evaluation_id is None
    assert evaluation.baseline_dataset_version_id is None


def test_evaluation_allows_paired_baseline() -> None:
    """同数据集基线：基线数据集版本通常等于自身数据集版本，但实体不强制相等
    （跨数据集对比正是合法场景），只强制与基线 ID 成对。"""
    dataset_version_id = uuid4()
    evaluation = Evaluation(
        **_evaluation_kwargs(
            dataset_version_id=dataset_version_id,
            baseline_evaluation_id=uuid4(),
            baseline_dataset_version_id=dataset_version_id,
        )
    )
    assert evaluation.baseline_dataset_version_id == dataset_version_id


def test_evaluation_allows_cross_dataset_paired_baseline() -> None:
    """跨数据集：两个数据集版本不同，同样合法。"""
    evaluation = Evaluation(
        **_evaluation_kwargs(
            baseline_evaluation_id=uuid4(),
            baseline_dataset_version_id=uuid4(),
        )
    )
    assert evaluation.baseline_dataset_version_id != evaluation.dataset_version_id


@pytest.mark.parametrize(
    ("baseline_evaluation_id", "baseline_dataset_version_id"),
    [(uuid4(), None), (None, uuid4())],
)
def test_evaluation_rejects_half_baseline_reference(
    baseline_evaluation_id: object, baseline_dataset_version_id: object
) -> None:
    """只有一半的基线引用 → 拒绝。

    与数据库的配对 CHECK 同一语义：应用层是权威校验，库层是防绕过兜底。
    """
    with pytest.raises(DomainValidationError, match="成对"):
        Evaluation(
            **_evaluation_kwargs(
                baseline_evaluation_id=baseline_evaluation_id,
                baseline_dataset_version_id=baseline_dataset_version_id,
            )
        )
