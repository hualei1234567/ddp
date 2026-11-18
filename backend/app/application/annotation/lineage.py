"""标注链血缘写边编排。

本模块只决定「什么时候登记哪些节点/边」，SQL、唯一约束与并发兜底在
``SqlLineageRepository``。沿用数据集侧 ``DatasetVersionLineageRecorder`` 的
同一套 ``ensure_node`` → ``ensure_edge`` 模式，不引入第二套图写入实现。

两类边（均为阶段 2 冻结契约里的既有关系，不新增枚举/不新增迁移）：

1. ``Episode --ANNOTATED_BY--> ANNOTATION``
   提交经复核或仲裁**通过**时，为每条标注记录（``AnnotationRecord``）写一条边。
   ``ANNOTATION`` 节点 ID 用标注记录 ID；``annotation_submission_id`` 与提交版本
   放进 ``metadata``，便于从边反查是哪一次提交产生的标注。
2. ``Episode --EVALUATED_BY--> QUALITY_RUN``
   质量检查运行完成时，为该提交涉及的每个 Episode 写一条边。``QUALITY_RUN``
   节点 ID 用真实的质量检查记录 ID（``QualityCheckRun.run_id``），``metadata``
   带检查结论与规则集版本。

**为什么质量边不在 Episode 的 ``record-quality`` 动作里写**：该动作只落一个
``quality_status`` 枚举，既没有质量运行实体也没有规则版本，硬造一个
``QUALITY_RUN`` 节点就是伪造关联。真正的质量运行事实在
``annotation_quality_runs``（含 ``rule_set_version``），因此写边放在质量检查
用例里，语义与数据都有权威来源。

边只追加：重复调用由 :meth:`LineageRecorder.ensure_edge` 幂等吸收，返回本次
**新建**的边数。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from uuid import UUID

from app.application.annotation.ports import LineageRecorder
from app.domain.annotation.submission import AnnotationRecord
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope


class AnnotationLineageWriteError(RuntimeError):
    """血缘写边失败：调用方必须回滚业务写入，不能只留业务事实。"""


class AnnotationLineageRecorder:
    """把标注通过、质量检查两类事实写进血缘图。"""

    def __init__(self, lineage: LineageRecorder) -> None:
        self._lineage = lineage

    def record_approved_annotations(
        self,
        scope: ProjectScope,
        *,
        submission_id: UUID,
        submission_version: int,
        records: Iterable[AnnotationRecord],
        now: datetime,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """登记 ``Episode --ANNOTATED_BY--> Annotation`` 边。

        ``records`` 必须是本次**刚被通过**的标注记录（调用方只传状态实际
        从 SUBMITTED 推进到 APPROVED 的那些），否则会把被驳回或被取代的
        记录也写成"已被标注"。
        """
        unique_records = _unique_by_annotation_id(records)
        edge_metadata: dict[str, object] = {
            "annotation_submission_id": str(submission_id),
            "submission_version": submission_version,
            **dict(metadata or {}),
        }
        created = 0
        for record in unique_records:
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.EPISODE,
                node_id=record.episode_id,
                now=now,
            )
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.ANNOTATION,
                node_id=record.annotation_id,
                now=now,
            )
            if self._lineage.ensure_edge(
                scope,
                from_node_type=LineageNodeType.EPISODE,
                from_node_id=record.episode_id,
                to_node_type=LineageNodeType.ANNOTATION,
                to_node_id=record.annotation_id,
                relation=LineageRelation.ANNOTATED_BY,
                metadata={
                    **edge_metadata,
                    "annotation_version": record.annotation_version,
                    "episode_version": record.episode_version,
                },
                now=now,
            ):
                created += 1
        return created

    def record_quality_run(
        self,
        scope: ProjectScope,
        *,
        submission_id: UUID,
        run_id: UUID,
        quality_status: str,
        rule_set_version: str,
        episode_ids: Iterable[UUID],
        now: datetime,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """登记 ``Episode --EVALUATED_BY--> QUALITY_RUN`` 边。

        ``run_id`` 必须是已落库的质量检查记录 ID；``quality_status`` 与
        ``rule_set_version`` 取自该记录本身。没有任何 Episode 时返回 0
        （空提交本就不该被检查，走到这里也不臆造边）。
        """
        unique_episodes = tuple(dict.fromkeys(episode_ids))
        if not unique_episodes:
            return 0
        edge_metadata: dict[str, object] = {
            "annotation_submission_id": str(submission_id),
            "quality_run_id": str(run_id),
            "quality_status": quality_status,
            "rule_set_version": rule_set_version,
            **dict(metadata or {}),
        }
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.QUALITY_RUN,
            node_id=run_id,
            now=now,
        )
        created = 0
        for episode_id in unique_episodes:
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.EPISODE,
                node_id=episode_id,
                now=now,
            )
            if self._lineage.ensure_edge(
                scope,
                from_node_type=LineageNodeType.EPISODE,
                from_node_id=episode_id,
                to_node_type=LineageNodeType.QUALITY_RUN,
                to_node_id=run_id,
                relation=LineageRelation.EVALUATED_BY,
                metadata=edge_metadata,
                now=now,
            ):
                created += 1
        return created


def _unique_by_annotation_id(records: Iterable[AnnotationRecord]) -> tuple[AnnotationRecord, ...]:
    """同一标注记录只写一条边；保留首次出现的顺序，结果确定。"""
    seen: set[UUID] = set()
    unique: list[AnnotationRecord] = []
    for record in records:
        if record.annotation_id in seen:
            continue
        seen.add(record.annotation_id)
        unique.append(record)
    return tuple(unique)


__all__ = ["AnnotationLineageRecorder", "AnnotationLineageWriteError"]
