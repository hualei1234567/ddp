"""Episode 创建、状态动作、Manifest 就绪与版本修订用例。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.ports import AuditRepository, OutboxRepository
from app.application.episodes.commands import (
    CreateEpisodeCommand,
    EpisodeActionCommand,
    MarkEpisodeReadyCommand,
    ReviseEpisodeCommand,
)
from app.application.episodes.ports import (
    EpisodeRawAssetRepository,
    EpisodeRepository,
    LineageRepository,
    UnitOfWork,
)
from app.application.ingestion.ports import IngestionJobRepository, RawAssetRepository
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import (
    ConflictError,
    DomainValidationError,
    EpisodeOverlapConflictError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.episodes.lineage import LineageEdge, validate_supersedes_linear
from app.domain.episodes.models import Episode, EpisodeRawAsset
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetStatus
from app.domain.ingestion.errors import AssetNotUsableError


def utcnow() -> datetime:
    return datetime.now(UTC)


class EpisodeService:
    def __init__(
        self,
        episodes: EpisodeRepository,
        episode_assets: EpisodeRawAssetRepository,
        lineage: LineageRepository,
        raw_assets: RawAssetRepository,
        jobs: IngestionJobRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._lineage = lineage
        self._raw_assets = raw_assets
        self._jobs = jobs
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def create(
        self,
        scope: ProjectScope,
        command: CreateEpisodeCommand,
        *,
        request_id: str | None = None,
    ) -> Episode:
        now = utcnow()
        raw_asset_ids = _unique_uuids(command.raw_asset_ids)
        if not 1 <= len(raw_asset_ids) <= 64:
            raise DomainValidationError("raw_asset_ids 数量必须在 1 到 64 之间")
        assets = []
        for asset_id in raw_asset_ids:
            asset = self._raw_assets.get(scope, asset_id)
            if asset is None:
                raise ResourceNotFoundError("原始资产", asset_id)
            if asset.status not in {RawAssetStatus.VALID, RawAssetStatus.ARCHIVED}:
                raise AssetNotUsableError("原始资产状态不可用于 Episode")
            assets.append(asset)
        start_time_ns, end_time_ns = command.segmentation.range
        if not command.segmentation.allow_overlap:
            for asset_id in raw_asset_ids:
                overlaps = self._episode_assets.find_overlaps(
                    scope,
                    asset_id=asset_id,
                    start_time_ns=start_time_ns,
                    end_time_ns=end_time_ns,
                )
                if overlaps:
                    raise EpisodeOverlapConflictError(
                        "同一原始资产的时间区间已被其他 Episode 使用",
                        details={"asset_id": str(asset_id)},
                    )
        for job_id in _unique_uuids(command.ingestion_job_ids):
            if self._jobs.get(scope, job_id) is None:
                raise ResourceNotFoundError("接入任务", job_id)

        episode_id = uuid4()
        code = command.code or f"EP-{episode_id.hex[:20]}"
        if self._episodes.get_by_code(scope, code) is not None:
            raise ConflictError("同项目 Episode code 已存在")
        episode = Episode(
            episode_id=episode_id,
            project_id=scope.project_id,
            code=code,
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            time_base=dict(command.time_base),
            canonical_schema_version=command.canonical_schema_version,
            pipeline_id=command.pipeline_id,
            pipeline_version=command.pipeline_version,
            segmentation_rule_version=command.segmentation.rule_version,
            calibration_snapshot=command.calibration_snapshot,
            task_id=command.task_id,
            batch_id=command.batch_id,
            robot_id=command.robot_id,
            device_ids=command.device_ids,
            sensor_ids=command.sensor_ids,
            scene_labels=command.scene_labels,
            language_instruction=command.language_instruction,
            success=command.success,
            failure_reason=command.failure_reason,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._episodes.add(scope, episode)
        rows = [
            EpisodeRawAsset(
                project_id=scope.project_id,
                episode_id=created.episode_id,
                asset_id=asset_id,
                role=command.evidence_role,
                sha256_snapshot=asset.sha256,
                size_bytes_snapshot=asset.size_bytes,
                start_time_ns=start_time_ns,
                end_time_ns=end_time_ns,
            )
            for asset_id, asset in zip(raw_asset_ids, assets, strict=True)
        ]
        self._episode_assets.add_many(scope, rows)
        self._register_episode_lineage(scope, created, command, now=now)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="EPISODE",
            resource_id=created.episode_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "raw_asset_ids": [str(item) for item in raw_asset_ids],
                "segmentation_rule_version": command.segmentation.rule_version,
            },
            event_type="ingestion.episode.created",
            event_payload={
                "episode_id": str(created.episode_id),
                "episode_lineage_id": str(created.episode_lineage_id),
                "episode_version": created.episode_version,
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def revise(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        command: ReviseEpisodeCommand,
        *,
        request_id: str | None = None,
    ) -> Episode:
        now = utcnow()
        current = self._load(scope, episode_id)
        if command.expected_version is not None and current.version != command.expected_version:
            raise VersionConflictError("Episode")
        new_id = uuid4()
        if self._episodes.get_by_code(scope, command.code) is not None:
            raise ConflictError("同项目 Episode code 已存在")
        revised = current.next_revision(episode_id=new_id, code=command.code, now=now)
        created = self._episodes.add(scope, revised)
        source_rows = self._episode_assets.list_for_episode(scope, current.episode_id)
        self._episode_assets.add_many(
            scope,
            [
                EpisodeRawAsset(
                    project_id=scope.project_id,
                    episode_id=new_id,
                    asset_id=row.asset_id,
                    role=row.role,
                    sha256_snapshot=row.sha256_snapshot,
                    size_bytes_snapshot=row.size_bytes_snapshot,
                    start_time_ns=row.start_time_ns,
                    end_time_ns=row.end_time_ns,
                )
                for row in source_rows
            ],
        )
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.EPISODE,
            node_id=new_id,
            now=now,
        )
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.EPISODE,
            node_id=current.episode_id,
            now=now,
        )
        self._append_edge(
            scope,
            from_type=LineageNodeType.EPISODE,
            from_id=new_id,
            to_type=LineageNodeType.EPISODE,
            to_id=current.episode_id,
            relation=LineageRelation.SUPERSEDES,
            now=now,
        )
        for row in source_rows:
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.RAW_ASSET,
                node_id=row.asset_id,
                now=now,
            )
            self._append_edge(
                scope,
                from_type=LineageNodeType.RAW_ASSET,
                from_id=row.asset_id,
                to_type=LineageNodeType.EPISODE,
                to_id=new_id,
                relation=LineageRelation.SEGMENTED_INTO,
                now=now,
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="EPISODE",
            resource_id=new_id,
            action="revise",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "supersedes_id": str(current.episode_id),
                "episode_lineage_id": str(created.episode_lineage_id),
                "episode_version": created.episode_version,
            },
            event_type="ingestion.episode.revised",
            event_payload={
                "episode_id": str(new_id),
                "supersedes_id": str(current.episode_id),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def mark_ready(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        command: MarkEpisodeReadyCommand,
        *,
        request_id: str | None = None,
    ) -> Episode:
        now = utcnow()
        episode = self._load(scope, episode_id)
        if episode.version != command.expected_version:
            raise VersionConflictError("Episode")
        ready = episode.mark_ready(
            manifest_key=command.manifest_key,
            manifest=command.manifest,
            frame_count=command.frame_count,
            now=now,
        )
        saved = self._episodes.save(scope, ready, expected_version=episode.version)
        self._record_action(scope, episode, saved, "system-ready", request_id, now)
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        command: EpisodeActionCommand,
        *,
        request_id: str | None = None,
    ) -> Episode:
        now = utcnow()
        episode = self._load(scope, episode_id)
        if episode.version != command.expected_version:
            raise VersionConflictError("Episode")
        action = command.action.strip()
        if action == "system-ready":
            raise DomainValidationError("system-ready 必须通过 mark_ready 并提交 Manifest")
        if action == "system-start":
            updated = episode.start_processing(now)
        elif action == "system-fail":
            updated = episode.mark_failed(
                error_code=_required(command.error_code, "error_code"),
                error_summary=_required(command.error_summary, "error_summary"),
                now=now,
            )
        elif action == "retry":
            updated = episode.retry_failed(now)
        elif action == "quality-check":
            updated = episode.begin_quality_check(now)
        elif action == "record-quality":
            updated = episode.record_quality_result(
                _required(command.quality_status, "quality_status"),
                now,
            )
        elif action == "accept":
            updated = episode.accept(now)
        elif action == "reject":
            updated = episode.reject(reason=_required(command.reason, "reason"), now=now)
        elif action == "complete":
            updated = episode.complete(now)
        elif action == "archive":
            updated = episode.archive(now)
        elif action == "invalidate":
            updated = episode.invalidate(reason=_required(command.reason, "reason"), now=now)
        else:
            raise DomainValidationError("未知 Episode 动作")
        saved = self._episodes.save(scope, updated, expected_version=episode.version)
        self._record_action(scope, episode, saved, action, request_id, now, reason=command.reason)
        self._uow.commit()
        return saved

    def _load(self, scope: ProjectScope, episode_id: UUID) -> Episode:
        episode = self._episodes.get(scope, episode_id)
        if episode is None:
            raise ResourceNotFoundError("Episode", episode_id)
        return episode

    def _register_episode_lineage(
        self,
        scope: ProjectScope,
        episode: Episode,
        command: CreateEpisodeCommand,
        *,
        now: datetime,
    ) -> None:
        self._lineage.ensure_node(
            scope,
            node_type=LineageNodeType.EPISODE,
            node_id=episode.episode_id,
            now=now,
        )
        for asset_id in _unique_uuids(command.raw_asset_ids):
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.RAW_ASSET,
                node_id=asset_id,
                now=now,
            )
            self._append_edge(
                scope,
                from_type=LineageNodeType.RAW_ASSET,
                from_id=asset_id,
                to_type=LineageNodeType.EPISODE,
                to_id=episode.episode_id,
                relation=LineageRelation.SEGMENTED_INTO,
                now=now,
            )
        for job_id in _unique_uuids(command.ingestion_job_ids):
            self._lineage.ensure_node(
                scope,
                node_type=LineageNodeType.INGESTION_JOB,
                node_id=job_id,
                now=now,
            )
            self._append_edge(
                scope,
                from_type=LineageNodeType.INGESTION_JOB,
                from_id=job_id,
                to_type=LineageNodeType.EPISODE,
                to_id=episode.episode_id,
                relation=LineageRelation.PRODUCED,
                now=now,
            )
            for asset_id in _unique_uuids(command.raw_asset_ids):
                self._append_edge(
                    scope,
                    from_type=LineageNodeType.INGESTION_JOB,
                    from_id=job_id,
                    to_type=LineageNodeType.RAW_ASSET,
                    to_id=asset_id,
                    relation=LineageRelation.USED_AS_INPUT,
                    now=now,
                )

    def _append_edge(
        self,
        scope: ProjectScope,
        *,
        from_type: LineageNodeType,
        from_id: UUID,
        to_type: LineageNodeType,
        to_id: UUID,
        relation: LineageRelation,
        now: datetime,
    ) -> None:
        if self._lineage.exists_edge(
            scope,
            from_node_type=from_type,
            from_node_id=from_id,
            to_node_type=to_type,
            to_node_id=to_id,
            relation=relation,
        ):
            return
        edge = LineageEdge(
            edge_id=uuid4(),
            project_id=scope.project_id,
            from_node_type=from_type,
            from_node_id=from_id,
            to_node_type=to_type,
            to_node_id=to_id,
            relation=relation,
            metadata={},
            created_by=scope.actor_id,
            created_at=now,
        )
        if relation is LineageRelation.SUPERSEDES:
            validate_supersedes_linear(self._lineage.list_supersedes_edges(scope), edge)
        self._lineage.add_edge(scope, edge)

    def _record_action(
        self,
        scope: ProjectScope,
        before: Episode,
        after: Episode,
        action: str,
        request_id: str | None,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="EPISODE",
            resource_id=after.episode_id,
            action=action,
            from_status=before.status.value,
            to_status=after.status.value,
            reason=reason,
            request_id=request_id,
            details={
                "episode_lineage_id": str(after.episode_lineage_id),
                "episode_version": after.episode_version,
            },
            event_type=f"ingestion.episode.{action}",
            event_payload={
                "episode_id": str(after.episode_id),
                "from_status": before.status.value,
                "to_status": after.status.value,
            },
            occurred_at=now,
        )


def _unique_uuids(values: object) -> tuple[UUID, ...]:
    if not isinstance(values, list | tuple):
        raise DomainValidationError("UUID 列表字段非法")
    result: list[UUID] = []
    seen: set[UUID] = set()
    for item in values:
        if not isinstance(item, UUID):
            raise DomainValidationError("列表只能包含 UUID")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _required(value: str | None, field_name: str) -> str:
    if value is None or not value.strip():
        raise DomainValidationError(f"{field_name} 不能为空")
    return value.strip()
