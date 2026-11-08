"""标注样本集用例：筛选预览、去重/分层、快照、消费与取消。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewAnnotationSampleSet
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationSampleSetRepository,
    AnnotationTaskRepository,
    AuditRepository,
    EpisodeDirectory,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    AnnotationSampleSetAction,
    AnnotationSampleSetStatus,
)
from app.domain.annotation.errors import (
    ConflictError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.sample_set import AnnotationSampleSet
from app.domain.annotation.selection import SelectionEpisode, select_episodes
from app.domain.annotation.state_machines import (
    allowed_sample_set_actions,
    assert_sample_set_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_SAMPLE_SET"
_ELIGIBLE_STATUSES = ("ACCEPTED", "COMPLETED")
_ELIGIBLE_QUALITY = ("PASS", "WARN")


class AnnotationSampleSetUseCases:
    def __init__(
        self,
        sample_sets: AnnotationSampleSetRepository,
        tasks: AnnotationTaskRepository,
        episodes: EpisodeDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._sample_sets = sample_sets
        self._tasks = tasks
        self._episodes = episodes
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, sample_set_id: UUID) -> AnnotationSampleSet:
        sample_set = self._sample_sets.get(scope, sample_set_id)
        if sample_set is None:
            raise ResourceNotFoundError("标注样本集", sample_set_id)
        return sample_set

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSampleSet]:
        if status is not None:
            try:
                AnnotationSampleSetStatus(status)
            except ValueError as exc:
                raise DomainValidationError("status 取值非法") from exc
        return self._sample_sets.list(scope, status=status, after_id=after_id, limit=limit)

    def allowed_actions(self, scope: ProjectScope, sample_set_id: UUID) -> frozenset[str]:
        return allowed_sample_set_actions(self.get(scope, sample_set_id).status)

    def create(
        self,
        scope: ProjectScope,
        command: NewAnnotationSampleSet,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSampleSet:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        sample_set = AnnotationSampleSet(
            sample_set_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            strategy=command.strategy,
            selection_rule_version=command.selection_rule_version,
            filters=command.filters,
            strata=command.strata,
            dedup_key=command.dedup_key,
            data_source=command.data_source,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._sample_sets.add(scope, sample_set)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.sample_set_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={"code": created.code},
            event_type="annotation.sample_set.created",
            event_payload={
                "sample_set_id": str(created.sample_set_id),
                "code": created.code,
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.sample_set_id),
            now=now,
        )
        self._uow.commit()
        return created

    def preview(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        *,
        expected_version: int,
        filters: Mapping[str, object] | None = None,
        strata: tuple[Mapping[str, object], ...] | None = None,
        warn_confirmed: bool = False,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSampleSet:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": "preview",
            "expected_version": expected_version,
            "filters": dict(filters or {}),
            "strata": [dict(item) for item in (strata or ())],
            "warn_confirmed": warn_confirmed,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        current = self.get(scope, sample_set_id)
        if current.version != expected_version:
            raise VersionConflictError("标注样本集")
        if current.status not in {
            AnnotationSampleSetStatus.DRAFT,
            AnnotationSampleSetStatus.PREVIEW,
        }:
            assert_sample_set_action(
                current.status,
                AnnotationSampleSetAction.PREVIEW.value,
            )
        merged_filters = dict(current.filters)
        merged_filters.update(dict(filters or {}))
        merged_strata = tuple(strata) if strata is not None else current.strata
        candidates = self._episodes.list_candidates(
            scope,
            statuses=_ELIGIBLE_STATUSES,
            quality_statuses=_ELIGIBLE_QUALITY,
            include_ids=_uuids(merged_filters.get("include_episode_ids")),
            exclude_ids=_uuids(merged_filters.get("exclude_episode_ids")),
            captured_from=_datetime(merged_filters.get("captured_from")),
            captured_to=_datetime(merged_filters.get("captured_to")),
        )
        result = select_episodes(
            [
                SelectionEpisode(
                    episode_id=item.episode_id,
                    episode_lineage_id=item.episode_lineage_id,
                    episode_version=item.episode_version,
                    status=item.status,
                    quality_status=item.quality_status,
                    manifest_sha256=item.manifest_sha256,
                    annotation_status="NOT_STARTED",
                    scene_labels=item.scene_labels,
                    robot_id=item.robot_id,
                    device_ids=item.device_ids,
                    sensor_ids=item.sensor_ids,
                    captured_at=item.captured_at,
                )
                for item in candidates
            ],
            project_id=scope.project_id,
            sample_set_id=current.sample_set_id,
            filters=merged_filters,
            strata=merged_strata,
            strategy=current.strategy,
            dedup_key=current.dedup_key,
            warn_confirmed=warn_confirmed,
            now=now,
        )
        gate_exclusions = self._episodes.exclusion_summary(
            scope,
            statuses=_ELIGIBLE_STATUSES,
            quality_statuses=_ELIGIBLE_QUALITY,
            include_ids=_uuids(merged_filters.get("include_episode_ids")),
            exclude_ids=_uuids(merged_filters.get("exclude_episode_ids")),
            captured_from=_datetime(merged_filters.get("captured_from")),
            captured_to=_datetime(merged_filters.get("captured_to")),
        )
        merged_exclusions = dict(result.exclusion_summary)
        for reason, count in gate_exclusions.items():
            merged_exclusions[reason] = merged_exclusions.get(reason, 0) + count
        updated = replace(
            current,
            filters=merged_filters,
            strata=merged_strata,
            updated_at=now,
            updated_by=scope.actor_id,
        ).preview(
            episode_refs=result.episode_refs,
            candidate_count=result.candidate_count,
            excluded_count=result.excluded_count + sum(gate_exclusions.values()),
            deduplicated_count=result.deduplicated_count,
            exclusion_summary=dict(sorted(merged_exclusions.items())),
            now=now,
        )
        self._sample_sets.replace_refs(scope, sample_set_id, result.episode_refs)
        saved = self._sample_sets.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.sample_set_id,
            action="preview",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={
                "candidate_count": saved.candidate_count,
                "selected_count": saved.selected_count,
                "excluded_count": saved.excluded_count,
                "deduplicated_count": saved.deduplicated_count,
            },
            event_type="annotation.sample_set.previewed",
            event_payload={
                "sample_set_id": str(saved.sample_set_id),
                "selected_count": saved.selected_count,
                "deduplicated_count": saved.deduplicated_count,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.sample_set_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def snapshot(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSampleSet:
        return self._action(
            scope,
            sample_set_id,
            AnnotationSampleSetAction.SNAPSHOT.value,
            expected_version=expected_version,
            reason=None,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )

    def consume(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSampleSet:
        if (
            self._tasks.count_active_for_sample_set(
                scope,
                sample_set_id=sample_set_id,
            )
            == 0
        ):
            raise ConflictError("任务未批准，不能消费样本集快照")
        return self._action(
            scope,
            sample_set_id,
            AnnotationSampleSetAction.CONSUME.value,
            expected_version=expected_version,
            reason=None,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )

    def cancel(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        *,
        expected_version: int,
        reason: str,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AnnotationSampleSet:
        if (
            self._tasks.count_active_for_sample_set(
                scope,
                sample_set_id=sample_set_id,
            )
            > 0
        ):
            raise ConflictError("样本集已被任务引用，不能取消")
        return self._action(
            scope,
            sample_set_id,
            AnnotationSampleSetAction.CANCEL.value,
            expected_version=expected_version,
            reason=reason,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )

    def _action(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None,
        idempotency_key: str | None,
        method: str,
        path: str,
        request_id: str | None,
    ) -> AnnotationSampleSet:
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._replay(scope, replay.response_ref)

        current = self.get(scope, sample_set_id)
        if current.version != expected_version:
            raise VersionConflictError("标注样本集")
        action_name = validate_action_name(action, family="sample_set")
        assert_sample_set_action(current.status, action_name)
        if action_name == AnnotationSampleSetAction.SNAPSHOT.value:
            updated = current.snapshot(now=now)
        else:
            updated = current.apply_action(action_name, reason=reason, now=now)
        updated = replace(updated, updated_by=scope.actor_id)
        saved = self._sample_sets.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=saved.sample_set_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"snapshot_sha256": saved.snapshot_sha256},
            event_type="annotation.sample_set.status_changed",
            event_payload={
                "sample_set_id": str(saved.sample_set_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "snapshot_sha256": saved.snapshot_sha256,
                "version": saved.version,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.sample_set_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> AnnotationSampleSet:
        if not response_ref:
            raise ResourceNotFoundError("标注样本集", response_ref)
        try:
            sample_set_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("标注样本集", response_ref) from exc
        return self.get(scope, sample_set_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuids(value: object) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("筛选条件必须是 UUID 列表")
    result: list[UUID] = []
    for item in value:
        try:
            result.append(item if isinstance(item, UUID) else UUID(str(item)))
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("筛选条件包含非法 UUID") from exc
    return tuple(result)


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DomainValidationError("时间筛选必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = ["AnnotationSampleSetUseCases"]
