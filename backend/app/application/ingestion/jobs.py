"""IngestionJob 创建、Worker 回写、重试与取消用例。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.ports import AuditRepository, OutboxRepository
from app.application.ingestion.commands import CreateIngestionJobCommand, JobActionCommand
from app.application.ingestion.ports import IngestionJobRepository, RawAssetRepository, UnitOfWork
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobType, RawAssetStatus
from app.domain.ingestion.errors import (
    AssetNotUsableError,
    ConflictError,
    DomainValidationError,
    IdempotencyKeyReusedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.ingestion.jobs import IngestionJob


def utcnow() -> datetime:
    return datetime.now(UTC)


class IngestionJobService:
    def __init__(
        self,
        jobs: IngestionJobRepository,
        raw_assets: RawAssetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._jobs = jobs
        self._raw_assets = raw_assets
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def create(
        self,
        scope: ProjectScope,
        command: CreateIngestionJobCommand,
        *,
        request_id: str | None = None,
    ) -> IngestionJob:
        now = utcnow()
        try:
            job_type = IngestionJobType(command.job_type)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("job_type 取值非法") from exc
        raw_asset_ids = _unique_uuids(command.raw_asset_ids)
        if len(raw_asset_ids) > 64:
            raise DomainValidationError("raw_asset_ids 不得超过 64 个")
        if not raw_asset_ids and command.input_manifest_key is None:
            raise DomainValidationError("接入任务至少需要一个原始资产或输入清单")
        job = IngestionJob(
            job_id=uuid4(),
            project_id=scope.project_id,
            job_type=job_type,
            raw_asset_ids=raw_asset_ids,
            input_manifest_key=command.input_manifest_key,
            idempotency_key=command.idempotency_key,
            timeout_sec=command.timeout_sec,
            max_attempts=command.max_attempts,
            pipeline_id=command.pipeline_id,
            pipeline_version=command.pipeline_version,
            parser_id=command.parser_id,
            parser_version=command.parser_version,
            config_hash=command.config_hash,
            metrics=command.metrics,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        if job.idempotency_key is not None:
            existing = self._jobs.get_by_idempotency_key(scope, job.idempotency_key)
            if existing is not None:
                return self._replay(existing, job)
        for asset_id in raw_asset_ids:
            asset = self._raw_assets.get(scope, asset_id)
            if asset is None:
                raise ResourceNotFoundError("原始资产", asset_id)
            allowed = {RawAssetStatus.VALID, RawAssetStatus.ARCHIVED}
            if job_type is IngestionJobType.VALIDATE:
                allowed.add(RawAssetStatus.VALIDATING)
            if asset.status not in allowed:
                raise AssetNotUsableError("原始资产状态不可用于该接入任务")
        try:
            created = self._jobs.add(scope, job)
        except ConflictError:
            if job.idempotency_key is None:
                raise
            existing = self._jobs.get_by_idempotency_key(scope, job.idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, job)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="INGESTION_JOB",
            resource_id=created.job_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "job_type": created.job_type.value,
                "raw_asset_ids": [str(item) for item in created.raw_asset_ids],
            },
            event_type="ingestion.job.created",
            event_payload={
                "job_id": str(created.job_id),
                "job_type": created.job_type.value,
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        job_id: UUID,
        command: JobActionCommand,
        *,
        request_id: str | None = None,
    ) -> IngestionJob:
        now = utcnow()
        job = self._load(scope, job_id)
        if job.version != command.expected_version:
            raise VersionConflictError("接入任务")
        action = command.action.strip()
        if action == "worker-start":
            updated = job.start(now)
        elif action == "worker-succeed":
            updated = job.succeed(
                now=now,
                output_refs=command.output_refs,
                log_ref=command.log_ref,
                metrics=command.metrics,
            )
        elif action == "worker-fail":
            updated = job.fail(
                error_code=_required(command.error_code, "error_code"),
                error_summary=_required(command.error_summary, "error_summary"),
                now=now,
                output_refs=command.output_refs,
                log_ref=command.log_ref,
            )
        elif action == "job-timeout":
            updated = job.fail_timeout(now)
        elif action == "retry":
            updated = job.retry(now)
        elif action == "cancel":
            _required(command.reason, "reason")
            updated = job.request_cancel(now)
        elif action == "worker-cancelled":
            updated = job.confirm_cancel(now)
        else:
            raise DomainValidationError("未知接入任务动作")
        saved = self._jobs.save(scope, updated, expected_version=job.version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="INGESTION_JOB",
            resource_id=saved.job_id,
            action=action,
            from_status=job.status.value,
            to_status=saved.status.value,
            reason=command.reason,
            request_id=request_id,
            details={
                "attempt": saved.attempt,
                "max_attempts": saved.max_attempts,
                "error_code": saved.error_code,
            },
            event_type=f"ingestion.job.{action}",
            event_payload={
                "job_id": str(saved.job_id),
                "from_status": job.status.value,
                "to_status": saved.status.value,
                "attempt": saved.attempt,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def get(self, scope: ProjectScope, job_id: UUID) -> IngestionJob:
        return self._load(scope, job_id)

    def _load(self, scope: ProjectScope, job_id: UUID) -> IngestionJob:
        job = self._jobs.get(scope, job_id)
        if job is None:
            raise ResourceNotFoundError("接入任务", job_id)
        return job

    def _replay(self, existing: IngestionJob, candidate: IngestionJob) -> IngestionJob:
        if not existing.matches_idempotent_request(candidate):
            raise IdempotencyKeyReusedError("同一幂等键对应了不同的接入任务请求")
        return existing


def _unique_uuids(values: object) -> tuple[UUID, ...]:
    if values is None:
        return ()
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
