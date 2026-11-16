"""Episode/血缘/接入 Job 应用层端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.episodes.enums import EpisodeStatus, LineageNodeType
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

    def save(
        self,
        scope: ProjectScope,
        episode: Episode,
        *,
        expected_version: int,
    ) -> Episode: ...


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
