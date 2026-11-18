"""数据血缘节点投影与只追加边。

边只追加、不更新、不删除；``from``/``to`` 必须已登记在同项目节点投影中。
``SUPERSEDES`` 必须线性且无环，其他关系不得成环由应用层拒绝自环。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID

from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import (
    DomainValidationError,
    LineageConflictError,
)

# 关系两端允许的节点类型；未列出的组合一律拒绝。
RELATION_ENDPOINTS: Mapping[
    LineageRelation,
    tuple[frozenset[LineageNodeType], frozenset[LineageNodeType]],
] = MappingProxyType(
    {
        LineageRelation.UPLOADED_BY: (
            frozenset({LineageNodeType.RAW_ASSET}),
            frozenset({LineageNodeType.UPLOAD_SESSION}),
        ),
        LineageRelation.PRODUCED_BY: (
            frozenset({LineageNodeType.RAW_ASSET}),
            frozenset({LineageNodeType.COLLECTION_TASK, LineageNodeType.COLLECTION_BATCH}),
        ),
        LineageRelation.USED_AS_INPUT: (
            frozenset({LineageNodeType.INGESTION_JOB}),
            frozenset({LineageNodeType.RAW_ASSET}),
        ),
        LineageRelation.PRODUCED: (
            frozenset({LineageNodeType.INGESTION_JOB}),
            frozenset({LineageNodeType.EPISODE}),
        ),
        LineageRelation.SEGMENTED_INTO: (
            frozenset({LineageNodeType.RAW_ASSET}),
            frozenset({LineageNodeType.EPISODE}),
        ),
        LineageRelation.SUPERSEDES: (
            frozenset({LineageNodeType.EPISODE}),
            frozenset({LineageNodeType.EPISODE}),
        ),
        LineageRelation.INVALIDATES: (
            frozenset({LineageNodeType.EPISODE}),
            # 上游 Episode 失效会让引用它的数据集版本失效（validity_status
            # STALE→INVALIDATED），契约要求这条失效必须用血缘边表达而不是
            # 改历史：`Episode --INVALIDATES--> DatasetVersion`。
            frozenset(
                {
                    LineageNodeType.EPISODE,
                    LineageNodeType.RAW_ASSET,
                    LineageNodeType.DATASET_VERSION,
                }
            ),
        ),
        LineageRelation.EVALUATED_BY: (
            frozenset({LineageNodeType.EPISODE}),
            frozenset({LineageNodeType.QUALITY_RUN}),
        ),
        LineageRelation.ANNOTATED_BY: (
            frozenset({LineageNodeType.EPISODE}),
            frozenset({LineageNodeType.ANNOTATION}),
        ),
        LineageRelation.INCLUDED_IN: (
            frozenset({LineageNodeType.EPISODE}),
            frozenset({LineageNodeType.DATASET_VERSION}),
        ),
        # 训练运行消费数据集版本：上游版本 → 下游外部训练引用节点
        # （``external_training_references.id``），见 ADR-0036。
        LineageRelation.TRAINED_ON: (
            frozenset({LineageNodeType.DATASET_VERSION}),
            frozenset({LineageNodeType.TRAINING_RUN}),
        ),
    }
)

# 数据源链的**精确**端点组合。
#
# 为什么不用上面的粗粒度白名单：``PRODUCED`` 的 from/to 集合是分别列举的，
# 再往里加 ``DATA_SOURCE`` / ``DATA_SOURCE_IMPORT`` 会顺带放行
# ``INGESTION_JOB --PRODUCED--> RAW_ASSET``、``DATA_SOURCE --PRODUCED-->
# RAW_ASSET`` 这类没有语义的组合。数据源链只有固定两跳，按组合精确列举既
# 表达真实规则，也不放宽任何既有组合。
IMPORT_CHAIN_ENDPOINTS: Mapping[
    LineageRelation,
    frozenset[tuple[LineageNodeType, LineageNodeType]],
] = MappingProxyType(
    {
        # 方向与既有一致：上游产出下游。这样 forward 能从数据源查到它产出了
        # 哪些资产，reverse 能从资产一路回溯到数据源，不必额外造逆向边。
        LineageRelation.PRODUCED: frozenset(
            {
                (LineageNodeType.DATA_SOURCE, LineageNodeType.DATA_SOURCE_IMPORT),
                (LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.RAW_ASSET),
            }
        ),
    }
)


def _enum(value: object, enum_type: type, field_name: str) -> object:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc


def _uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def _aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LineageNodeRef:
    project_id: UUID
    node_type: LineageNodeType | str
    node_id: UUID
    is_active: bool = True
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _uuid(self.project_id, "project_id"))
        object.__setattr__(self, "node_type", _enum(self.node_type, LineageNodeType, "node_type"))
        object.__setattr__(self, "node_id", _uuid(self.node_id, "node_id"))
        if not isinstance(self.is_active, bool):
            raise DomainValidationError("is_active 必须是布尔值")
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", _aware(self.updated_at, "updated_at"))


@dataclass(frozen=True, slots=True)
class LineageEdge:
    edge_id: UUID
    project_id: UUID
    from_node_type: LineageNodeType | str
    from_node_id: UUID
    to_node_type: LineageNodeType | str
    to_node_id: UUID
    relation: LineageRelation | str
    created_by: UUID
    created_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("edge_id", "project_id", "from_node_id", "to_node_id", "created_by"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "from_node_type",
            _enum(self.from_node_type, LineageNodeType, "from_node_type"),
        )
        object.__setattr__(
            self,
            "to_node_type",
            _enum(self.to_node_type, LineageNodeType, "to_node_type"),
        )
        object.__setattr__(self, "relation", _enum(self.relation, LineageRelation, "relation"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        if not isinstance(self.metadata, Mapping):
            raise DomainValidationError("metadata 必须是对象")
        object.__setattr__(self, "metadata", dict(self.metadata))
        validate_edge_endpoints(self)

    @property
    def from_key(self) -> tuple[str, UUID]:
        return (self.from_node_type.value, self.from_node_id)

    @property
    def to_key(self) -> tuple[str, UUID]:
        return (self.to_node_type.value, self.to_node_id)


def validate_edge_endpoints(edge: LineageEdge) -> None:
    if edge.from_node_type is edge.to_node_type and edge.from_node_id == edge.to_node_id:
        raise LineageConflictError("血缘边不允许指向自身")
    pair = (edge.from_node_type, edge.to_node_type)
    if pair in IMPORT_CHAIN_ENDPOINTS.get(edge.relation, frozenset()):
        return
    allowed_from, allowed_to = RELATION_ENDPOINTS[edge.relation]
    if edge.from_node_type not in allowed_from or edge.to_node_type not in allowed_to:
        raise LineageConflictError("血缘关系两端节点类型不匹配")


def validate_supersedes_linear(
    existing: Iterable[LineageEdge],
    candidate: LineageEdge | None = None,
) -> None:
    """校验 SUPERSEDES 边单入单出且无环。"""
    edges = [edge for edge in existing if edge.relation is LineageRelation.SUPERSEDES]
    if candidate is not None and candidate.relation is LineageRelation.SUPERSEDES:
        edges.append(candidate)
    outgoing: dict[tuple[str, UUID], tuple[str, UUID]] = {}
    incoming: set[tuple[str, UUID]] = set()
    for edge in edges:
        source = edge.from_key
        target = edge.to_key
        if source in outgoing:
            raise LineageConflictError("SUPERSEDES 同一版本只能有一个前驱")
        if target in incoming:
            raise LineageConflictError("SUPERSEDES 同一前驱只能有一个后继")
        outgoing[source] = target
        incoming.add(target)
    for start in outgoing:
        seen: set[tuple[str, UUID]] = set()
        current = start
        while current in outgoing:
            if current in seen:
                raise LineageConflictError("SUPERSEDES 不得成环")
            seen.add(current)
            current = outgoing[current]
