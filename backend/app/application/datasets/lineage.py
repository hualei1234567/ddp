"""DatasetVersion 血缘登记编排。

职责很小但必须单独成文件：把数据集域产生的血缘边统一写进血缘图——
版本转 READY 时的 ``Episode → DatasetVersion``（``INCLUDED_IN``），以及外部训练
引用创建时的 ``DatasetVersion → TrainingRun``（``TRAINED_ON``，ADR-0036）。规则要点：

1. **两端节点都必须先登记**。血缘表的两端有指向 ``lineage_node_refs`` 的复合外键，
   未登记节点写边会被数据库拒绝；这里按"先节点、后边"的顺序显式登记。
2. **幂等**。重复 validate（例如 VALIDATION_FAILED 后重试）或同一幂等键重放
   不得重复写边；判定交给仓储的 :meth:`ensure_edge`，它在唯一约束冲突时回读确认。
3. **不修改 dataset_items**。条目写入语义保持原有实现，本模块只读条目的
   ``episode_id``。

为什么放在 application：编排（先登记谁、写哪些边）属于用例，仓储细节
（SQL、唯一约束映射）在 infrastructure；不过本适配器本身没有仓储实现，
只组合端口，因此留在 application。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from uuid import UUID

from app.application.datasets.ports import LineageRecorder
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope


class DatasetVersionLineageRecorder:
    """把数据集域的血缘节点与边写入血缘图（INCLUDED_IN / INVALIDATES / TRAINED_ON）。"""

    def __init__(self, lineage: LineageRecorder) -> None:
        self._lineage = lineage

    def record_invalidation(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        episode_ids: Iterable[UUID],
        now: datetime,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """登记 ``Episode --INVALIDATES--> DatasetVersion`` 失效边。

        数据集版本被人工标记失效时，契约要求"失效必须通过追加血缘边表达，
        不删除历史"。方向取"上游 Episode → 下游 DatasetVersion"：失效的
        原因在上游，被阻断的是数据集版本。
        """
        unique_episodes = tuple(dict.fromkeys(episode_ids))
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.DATASET_VERSION,
            node_id=dataset_version_id,
            now=now,
        )
        edge_metadata: dict[str, object] = dict(metadata or {})
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
                to_node_type=LineageNodeType.DATASET_VERSION,
                to_node_id=dataset_version_id,
                relation=LineageRelation.INVALIDATES,
                metadata=edge_metadata,
                now=now,
            ):
                created += 1
        return created

    def record_training_run(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        training_run_id: UUID,
        now: datetime,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        """登记 ``DatasetVersion --TRAINED_ON--> TrainingRun`` 边。

        ``training_run_id`` 是平台内代表一次训练的 ``external_ref_id``（ADR-0036）。
        返回**本次新建**的边是否写入；边已存在（幂等重放）返回 ``False``。
        节点登记幂等，重复调用无副作用。
        """
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.DATASET_VERSION,
            node_id=dataset_version_id,
            now=now,
        )
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.TRAINING_RUN,
            node_id=training_run_id,
            now=now,
        )
        return self._lineage.ensure_edge(
            scope,
            from_node_type=LineageNodeType.DATASET_VERSION,
            from_node_id=dataset_version_id,
            to_node_type=LineageNodeType.TRAINING_RUN,
            to_node_id=training_run_id,
            relation=LineageRelation.TRAINED_ON,
            metadata=dict(metadata or {}),
            now=now,
        )

    def record_inclusion(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        episode_ids: Iterable[UUID],
        now: datetime,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """登记 ``DatasetVersion`` 节点与每条 ``Episode → DatasetVersion`` 边。

        返回**本次新建**的边数；重复调用返回 0。节点登记幂等，重复调用无副作用。
        """
        unique_episodes = tuple(dict.fromkeys(episode_ids))
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.DATASET_VERSION,
            node_id=dataset_version_id,
            now=now,
        )
        edge_metadata: dict[str, object] = dict(metadata or {})
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
                to_node_type=LineageNodeType.DATASET_VERSION,
                to_node_id=dataset_version_id,
                relation=LineageRelation.INCLUDED_IN,
                metadata=edge_metadata,
                now=now,
            ):
                created += 1
        return created
