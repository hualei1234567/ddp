"""标注工作进度投影：任务与批次的计数从提交事实重算。

**为什么要有这一层**

任务验收（``complete``）要求 ``progress.approved_items >= target_item_count``，
但此前没有任何代码写入 ``approved_items``——进度永远是 0，验收只能靠豁免说明，
等于"验收数量"这件事从未被真正核对。批次的 ``submitted/approved/rejected_item_count``
同样一直是 0，界面上的进度条与实际提交完全无关。

**口径**

- 只统计"当前有效"的提交：``DRAFT`` / ``WITHDRAWN`` / ``SUPERSEDED`` 不计
  （它们要么没提交，要么已被新版本取代；重复计入会把一件工作算两次）。
- 计数按 ``item_count`` 累加，不是按提交条数——一个提交可能覆盖多个条目。
- 一个提交只能落进一个桶；仲裁中的提交记 ``arbitration_items``，不重复计入
  ``submitted_items`` 之外的桶。
- 值没变就不写库：进度是派生数据，无变化时推进版本只会制造乐观锁冲突。

**触发点**

提交状态只要变化（提交、撤回、复核结论、仲裁结论），调用
:meth:`AnnotationProgressRefresher.refresh_for_submission`。三个用例各自在
"提交状态落库之后"调用同一个入口，规则只在这里定义一次。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from app.application.annotation.ports import (
    AnnotationBatchRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
)
from app.domain.annotation.enums import AnnotationSubmissionStatus
from app.domain.annotation.task import PROGRESS_FIELDS
from app.domain.identity.models import ProjectScope

# 提交状态 → 结论桶。未列出的状态（DRAFT/WITHDRAWN/SUPERSEDED）不计。
#
# ``submitted_items`` 是**累计已提交**（所有非草稿/撤回/被取代的提交），
# 结论桶（approved/rejected/rework/arbitration）是其中的细分。两者不是互斥
# 关系：复核通过后 submitted 必须仍然计着这件工作，否则界面会显示
# "已提交 4、通过 1"，而实际提交过 5 件。
_TASK_BUCKETS: dict[AnnotationSubmissionStatus, str] = {
    AnnotationSubmissionStatus.APPROVED: "approved_items",
    AnnotationSubmissionStatus.RESOLVED_APPROVED: "approved_items",
    AnnotationSubmissionStatus.REJECTED: "rejected_items",
    AnnotationSubmissionStatus.RESOLVED_REJECTED: "rejected_items",
    AnnotationSubmissionStatus.REWORK_REQUIRED: "rework_items",
    AnnotationSubmissionStatus.RESOLVED_REWORK: "rework_items",
    AnnotationSubmissionStatus.ESCALATED: "arbitration_items",
    AnnotationSubmissionStatus.ARBITRATING: "arbitration_items",
}

# "已提交过" = 在审 + 已有结论；只有草稿、撤回与被取代不算。
_COUNTED = frozenset(_TASK_BUCKETS) | {
    AnnotationSubmissionStatus.SUBMITTED,
    AnnotationSubmissionStatus.IN_REVIEW,
}
_PAGE_SIZE = 500


def _status(value: object) -> AnnotationSubmissionStatus | None:
    try:
        return AnnotationSubmissionStatus(getattr(value, "value", value))
    except ValueError:
        return None


def compute_task_progress(
    submissions: Iterable[object],
    *,
    total_items: int,
) -> dict[str, int]:
    """按有效提交重算任务进度；``total_items`` 是任务目标件数。"""
    counts = {field: 0 for field in PROGRESS_FIELDS}
    counts["total_items"] = max(0, int(total_items))
    for submission in submissions:
        status = _status(getattr(submission, "status", None))
        if status is None or status not in _COUNTED:
            continue
        item_count = int(getattr(submission, "item_count", 0) or 0)
        counts["submitted_items"] += item_count
        bucket = _TASK_BUCKETS.get(status)
        if bucket is not None:
            counts[bucket] += item_count
    return counts


def compute_batch_counts(
    submissions: Iterable[object],
) -> dict[str, int]:
    """按有效提交重算批次计数。

    不含 ``rework_count``：那个字段是"返工件数"以外的返工轮次，由提交用例
    在 ``require-rework`` 时自增，按提交事实重算会把轮次抹成件数。
    """
    submitted = approved = rejected = 0
    for submission in submissions:
        status = _status(getattr(submission, "status", None))
        if status not in _COUNTED:
            continue
        item_count = int(getattr(submission, "item_count", 0) or 0)
        submitted += item_count
        if status in {
            AnnotationSubmissionStatus.APPROVED,
            AnnotationSubmissionStatus.RESOLVED_APPROVED,
        }:
            approved += item_count
        elif status in {
            AnnotationSubmissionStatus.REJECTED,
            AnnotationSubmissionStatus.RESOLVED_REJECTED,
        }:
            rejected += item_count
    return {
        "submitted_item_count": submitted,
        "approved_item_count": approved,
        "rejected_item_count": rejected,
    }


class AnnotationProgressRefresher:
    """把提交事实投影到任务与批次的计数上。

    只写派生字段；不写审计、不发事件——审计关心的是"谁把提交判成什么"，
    进度变化本身就是那次操作的结果，重复记录只会制造噪音。
    """

    def __init__(
        self,
        tasks: AnnotationTaskRepository,
        batches: AnnotationBatchRepository,
        submissions: AnnotationSubmissionRepository,
    ) -> None:
        self._tasks = tasks
        self._batches = batches
        self._submissions = submissions

    def refresh_for_submission(
        self,
        scope: ProjectScope,
        submission: object,
        *,
        now: datetime,
    ) -> None:
        task_id = getattr(submission, "task_id", None)
        batch_id = getattr(submission, "batch_id", None)
        if isinstance(task_id, UUID):
            self.refresh_task(scope, task_id, now=now)
        if isinstance(batch_id, UUID):
            self.refresh_batch(scope, batch_id, now=now)

    def refresh_task(self, scope: ProjectScope, task_id: UUID, *, now: datetime) -> None:
        task = self._tasks.get(scope, task_id)
        if task is None:
            return
        # 仓储单页上限 501：进度必须覆盖任务下全部提交，按游标取完而不是
        # 传一个会被拒绝的大 limit，也不是只看第一页就当作全部。
        submissions: list[object] = []
        cursor: UUID | None = None
        while True:
            page = self._submissions.list_for_task(
                scope, task_id=task_id, after_id=cursor, limit=_PAGE_SIZE
            )
            submissions.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            cursor = getattr(page[-1], "submission_id", None)
            if not isinstance(cursor, UUID):
                break
        progress = compute_task_progress(submissions, total_items=task.target_item_count)
        if dict(task.progress or {}) == progress:
            return
        self._tasks.update_progress(scope, task_id, progress)

    def refresh_batch(self, scope: ProjectScope, batch_id: UUID, *, now: datetime) -> None:
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            return
        submissions = self._submissions.list_for_batch(scope, batch_id=batch_id)
        counts = compute_batch_counts(submissions)
        if all(getattr(batch, name) == value for name, value in counts.items()):
            return
        self._batches.update_counts(scope, batch_id, counts)


__all__ = [
    "AnnotationProgressRefresher",
    "compute_batch_counts",
    "compute_task_progress",
]
