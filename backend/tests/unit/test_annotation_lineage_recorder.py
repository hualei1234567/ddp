"""标注链血缘写边编排的单元测试（不依赖数据库）。

这里用内存假实现替代血缘仓储，只验证编排的**事实选择**：

- 只把调用方传入（刚被通过）的记录写成边，不自行扩大范围；
- 节点先登记、边后写入；
- ``metadata`` 带得出追溯所需的提交 ID / 版本 / 结论 / 规则集版本；
- 重复调用不重复建边（幂等由 ``ensure_edge`` 的返回值决定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from app.application.annotation.lineage import AnnotationLineageRecorder
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.identity.models import ProjectScope

_NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@dataclass
class _EdgeCall:
    from_node_type: LineageNodeType
    from_node_id: UUID
    to_node_type: LineageNodeType
    to_node_id: UUID
    relation: LineageRelation
    metadata: dict[str, object]


class _FakeLineage:
    """最小血缘写端口：按端点唯一，重复写返回 False。"""

    def __init__(self) -> None:
        self.nodes: list[tuple[LineageNodeType, UUID]] = []
        self.edges: list[_EdgeCall] = []

    def ensure_node(self, scope: ProjectScope, *, node_type: object, node_id: UUID, now: datetime):
        assert scope.project_id is not None
        assert now == _NOW
        self.nodes.append((LineageNodeType(node_type), node_id))
        return SimpleNamespace(node_type=node_type, node_id=node_id)

    def ensure_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: object,
        from_node_id: UUID,
        to_node_type: object,
        to_node_id: UUID,
        relation: object,
        metadata: Any,
        now: datetime,
    ) -> bool:
        assert now == _NOW
        key = (
            LineageNodeType(from_node_type),
            from_node_id,
            LineageNodeType(to_node_type),
            to_node_id,
            LineageRelation(relation),
        )
        if any(
            (
                call.from_node_type,
                call.from_node_id,
                call.to_node_type,
                call.to_node_id,
                call.relation,
            )
            == key
            for call in self.edges
        ):
            return False
        self.edges.append(
            _EdgeCall(
                from_node_type=LineageNodeType(from_node_type),
                from_node_id=from_node_id,
                to_node_type=LineageNodeType(to_node_type),
                to_node_id=to_node_id,
                relation=LineageRelation(relation),
                metadata=dict(metadata),
            )
        )
        return True


def _record(*, episode_id: UUID | None = None, annotation_id: UUID | None = None):
    return SimpleNamespace(
        annotation_id=annotation_id or uuid4(),
        episode_id=episode_id or uuid4(),
        annotation_version=2,
        episode_version=3,
    )


def test_approved_annotations_write_one_edge_per_record() -> None:
    lineage = _FakeLineage()
    recorder = AnnotationLineageRecorder(lineage)
    scope = ProjectScope(uuid4(), actor_id=uuid4())
    submission_id = uuid4()
    record = _record()
    duplicate = _record(annotation_id=record.annotation_id)

    created = recorder.record_approved_annotations(
        scope,
        submission_id=submission_id,
        submission_version=5,
        records=[record, duplicate],
        now=_NOW,
    )

    assert created == 1
    assert lineage.edges == [
        _EdgeCall(
            from_node_type=LineageNodeType.EPISODE,
            from_node_id=record.episode_id,
            to_node_type=LineageNodeType.ANNOTATION,
            to_node_id=record.annotation_id,
            relation=LineageRelation.ANNOTATED_BY,
            metadata={
                "annotation_submission_id": str(submission_id),
                "submission_version": 5,
                "annotation_version": 2,
                "episode_version": 3,
            },
        )
    ]
    # 节点必须先于边登记：Episode 与 Annotation 各一次。
    assert lineage.nodes == [
        (LineageNodeType.EPISODE, record.episode_id),
        (LineageNodeType.ANNOTATION, record.annotation_id),
    ]


def test_repeated_approved_annotation_write_is_idempotent() -> None:
    lineage = _FakeLineage()
    recorder = AnnotationLineageRecorder(lineage)
    scope = ProjectScope(uuid4(), actor_id=uuid4())
    submission_id = uuid4()
    record = _record()

    first = recorder.record_approved_annotations(
        scope,
        submission_id=submission_id,
        submission_version=5,
        records=[record],
        now=_NOW,
    )
    second = recorder.record_approved_annotations(
        scope,
        submission_id=submission_id,
        submission_version=5,
        records=[record],
        now=_NOW,
    )

    assert (first, second) == (1, 0)
    assert len(lineage.edges) == 1


def test_quality_run_writes_one_edge_per_episode_with_run_metadata() -> None:
    lineage = _FakeLineage()
    recorder = AnnotationLineageRecorder(lineage)
    scope = ProjectScope(uuid4(), actor_id=uuid4())
    submission_id, run_id = uuid4(), uuid4()
    first, second = uuid4(), uuid4()

    created = recorder.record_quality_run(
        scope,
        submission_id=submission_id,
        run_id=run_id,
        quality_status="PASSED",
        rule_set_version="sha256:" + "a" * 64,
        episode_ids=[first, second, first],
        now=_NOW,
    )

    assert created == 2
    assert {(edge.from_node_id, edge.to_node_id) for edge in lineage.edges} == {
        (first, run_id),
        (second, run_id),
    }
    assert all(edge.relation is LineageRelation.EVALUATED_BY for edge in lineage.edges)
    for edge in lineage.edges:
        assert edge.from_node_type is LineageNodeType.EPISODE
        assert edge.to_node_type is LineageNodeType.QUALITY_RUN
        assert edge.metadata == {
            "annotation_submission_id": str(submission_id),
            "quality_run_id": str(run_id),
            "quality_status": "PASSED",
            "rule_set_version": "sha256:" + "a" * 64,
        }
    assert lineage.nodes.count((LineageNodeType.QUALITY_RUN, run_id)) == 1


def test_quality_run_without_episodes_writes_nothing() -> None:
    lineage = _FakeLineage()
    recorder = AnnotationLineageRecorder(lineage)
    scope = ProjectScope(uuid4(), actor_id=uuid4())

    created = recorder.record_quality_run(
        scope,
        submission_id=uuid4(),
        run_id=uuid4(),
        quality_status="PASSED",
        rule_set_version="sha256:" + "a" * 64,
        episode_ids=[],
        now=_NOW,
    )

    assert created == 0
    assert lineage.nodes == []
    assert lineage.edges == []
