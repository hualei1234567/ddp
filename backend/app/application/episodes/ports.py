"""Episode/血缘/接入 Job 应用层端口。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.episodes.enums import EpisodeStatus, LineageDirection, LineageNodeType
from app.domain.episodes.filters import EpisodeListFilters
from app.domain.episodes.lineage import LineageEdge, LineageNodeRef
from app.domain.episodes.models import Episode, EpisodeRawAsset
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class EpisodeRepository(Protocol):
    def add(self, scope: ProjectScope, episode: Episode) -> Episode: ...

    def get(self, scope: ProjectScope, episode_id: UUID) -> Episode | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Episode | None: ...

    def get_by_lineage_version(
        self,
        scope: ProjectScope,
        *,
        episode_lineage_id: UUID,
        episode_version: int,
    ) -> Episode | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: EpisodeStatus | None = None,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        filters: EpisodeListFilters | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Episode]: ...

    def count_by_task(
        self,
        scope: ProjectScope,
        task_id: UUID,
    ) -> dict[str, int]:
        """按采集任务统计 Episode：``total`` 与 ``accepted``（ACCEPTED/COMPLETED）。

        采集任务的 ``submitted_episode_count`` / ``accepted_episode_count`` 此前
        从来没人写，``complete`` 的"验收数量达标"判定永远不成立、只能靠豁免。
        计数从 Episode 事实重算，而不是在每次状态迁移里加一（那样漏一次就永久偏）。
        """
        ...

    def counts_by_task(
        self,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> dict[UUID, dict[str, int]]:
        """批量版本：列表页一次查询拿到多个任务的计数，避免 N+1。"""
        ...


class EpisodeRawAssetRepository(Protocol):
    def add_many(
        self,
        scope: ProjectScope,
        rows: list[EpisodeRawAsset],
    ) -> list[EpisodeRawAsset]: ...

    def list_for_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
    ) -> list[EpisodeRawAsset]: ...

    def list_for_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> list[EpisodeRawAsset]: ...

    def find_overlaps(
        self,
        scope: ProjectScope,
        *,
        asset_id: UUID,
        start_time_ns: int,
        end_time_ns: int,
        exclude_episode_id: UUID | None = None,
    ) -> list[EpisodeRawAsset]: ...


class LineageRepository(Protocol):
    def ensure_node(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        now: datetime,
    ) -> LineageNodeRef: ...

    def get_node(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
    ) -> LineageNodeRef | None: ...

    def mark_inactive(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        now: datetime,
    ) -> None: ...

    def add_edge(self, scope: ProjectScope, edge: LineageEdge) -> LineageEdge: ...

    def exists_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: LineageNodeType,
        from_node_id: UUID,
        to_node_type: LineageNodeType,
        to_node_id: UUID,
        relation: object,
    ) -> bool: ...

    def list_supersedes_edges(self, scope: ProjectScope) -> list[LineageEdge]: ...

    def list_edges_from(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        limit: int = 100,
    ) -> list[LineageEdge]: ...

    def list_edges_to(
        self,
        scope: ProjectScope,
        *,
        node_type: LineageNodeType,
        node_id: UUID,
        limit: int = 100,
    ) -> list[LineageEdge]: ...

    def list_edges_for_nodes(
        self,
        scope: ProjectScope,
        *,
        nodes: Sequence[tuple[LineageNodeType, UUID]],
        direction: LineageDirection,
        limit: int,
    ) -> list[LineageEdge]:
        """一次取多个节点在当前方向的邻边（图查询批量入口）。

        实现必须：按 ``scope.project_id`` 过滤、按 ``(created_at, id)`` 排序、
        不超过 ``limit`` 行；不得逐节点查询。图查询由调用方按块传入节点，
        以这一条端口承载"批量而非 N+1"的约束。
        """
