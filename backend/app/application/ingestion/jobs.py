"""IngestionJob 创建、Worker 回写、重试与取消用例。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import (
    AuditRepository,
    IdempotencyRecord,
    IdempotencyRepository,
    OutboxRepository,
)
from app.application.ingestion.commands import CreateIngestionJobCommand, JobActionCommand
from app.application.ingestion.ports import IngestionJobRepository, RawAssetRepository, UnitOfWork
from app.domain.collection.errors import DomainValidationError as PaginationValidationError
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobStatus, IngestionJobType, RawAssetStatus
from app.domain.ingestion.errors import (
    AssetNotUsableError,
    ConflictError,
    DomainValidationError,
    IdempotencyKeyReusedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.ingestion.jobs import IngestionJob

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与 ``apply_action`` 的显式分支一致：未知动作先抛 ``DomainValidationError``，
# 不会走到按动作取名这一步，因此映射缺键在正常情况下不可达。
INGESTION_JOB_ACTION_EVENTS: dict[str, str] = {
    "worker-start": "ingestion.job.worker-start",
    "worker-succeed": "ingestion.job.worker-succeed",
    "worker-fail": "ingestion.job.worker-fail",
    "job-timeout": "ingestion.job.job-timeout",
    "retry": "ingestion.job.retry",
    "cancel": "ingestion.job.cancel",
    "worker-cancelled": "ingestion.job.worker-cancelled",
}


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
        *,
        idempotency: IdempotencyRepository | None = None,
    ) -> None:
        self._jobs = jobs
        self._raw_assets = raw_assets
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._guard = IdempotencyGuard(idempotency) if idempotency is not None else None
        # 幂等重放标记供 API 设置响应头；服务实例按请求装配，不跨请求共享。
        self.last_replayed = False

    def create(
        self,
        scope: ProjectScope,
        command: CreateIngestionJobCommand,
        *,
        request_id: str | None = None,
        http_idempotency_key: str | None = None,
        method: str = "POST",
        path: str | None = None,
    ) -> IngestionJob:
        now = utcnow()
        key = validate_key(http_idempotency_key)
        body = asdict(command)
        self.last_replayed = False
        replay = self._lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            self.last_replayed = True
            return self._load(scope, _uuid_ref(replay.response_ref, "接入任务"))

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
                self.last_replayed = True
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
            self.last_replayed = True
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
                # Worker 需要 pipeline/版本信息才能选择处理逻辑，随事件带过去
                # 可以避免 Worker 再查一次库（也能保证"用当时声明的版本跑"）。
                "pipeline_id": created.pipeline_id,
                "pipeline_version": created.pipeline_version,
                "parser_id": created.parser_id,
                "parser_version": created.parser_version,
                "config_hash": created.config_hash,
                # 供 Worker 恢复 trace_id；附加字段，既有字段语义不变。
                **({"request_id": request_id} if request_id is not None else {}),
            },
            occurred_at=now,
            # 投递类：此前是留痕类，于是**建了任务没有任何进程去执行**——
            # IngestionJob 有完整 API、状态机与 worker-* 动作，却没有消费者，
            # 等于一条只会记录"我要处理"而永远不处理的事件。
            dispatch=True,
        )
        self._store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            ref=created.job_id,
            now=now,
            response_status=201,
        )
        self._uow.commit()
        return created

    def list_jobs(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        raw_asset_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[IngestionJob]:
        bounded = _normalize_limit(limit)
        items = self._jobs.list(
            scope,
            status=_job_status(status),
            raw_asset_id=raw_asset_id,
            after_id=_decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=lambda item: item.job_id)

    def apply_action(
        self,
        scope: ProjectScope,
        job_id: UUID,
        command: JobActionCommand,
        *,
        request_id: str | None = None,
        http_idempotency_key: str | None = None,
        method: str = "POST",
        path: str | None = None,
    ) -> IngestionJob:
        now = utcnow()
        key = validate_key(http_idempotency_key)
        body = {
            "action": command.action,
            "expected_version": command.expected_version,
            "reason": command.reason,
            "error_code": command.error_code,
            "error_summary": command.error_summary,
            "log_ref": command.log_ref,
            "metrics": dict(command.metrics),
        }
        self.last_replayed = False
        replay = self._lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            self.last_replayed = True
            return self._load(scope, _uuid_ref(replay.response_ref, "接入任务"))

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
            event_type=INGESTION_JOB_ACTION_EVENTS[action],
            event_payload={
                "job_id": str(saved.job_id),
                "from_status": job.status.value,
                "to_status": saved.status.value,
                "attempt": saved.attempt,
            },
            occurred_at=now,
        )
        self._store(scope, key, method=method, path=path, body=body, ref=saved.job_id, now=now)
        self._uow.commit()
        return saved

    def get(self, scope: ProjectScope, job_id: UUID) -> IngestionJob:
        return self._load(scope, job_id)

    def _load(self, scope: ProjectScope, job_id: UUID) -> IngestionJob:
        job = self._jobs.get(scope, job_id)
        if job is None:
            raise ResourceNotFoundError("接入任务", job_id)
        return job

    def _lookup(
        self,
        scope: ProjectScope,
        key: str | None,
        *,
        method: str,
        path: str | None,
        body: dict[str, object],
        now: datetime,
    ) -> IdempotencyRecord | None:
        if self._guard is None or key is None or path is None:
            return None
        return self._guard.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )

    def _store(
        self,
        scope: ProjectScope,
        key: str | None,
        *,
        method: str,
        path: str | None,
        body: dict[str, object],
        ref: UUID,
        now: datetime,
        response_status: int = 200,
    ) -> None:
        if self._guard is None or key is None or path is None:
            return
        self._guard.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=response_status,
            response_ref=str(ref),
            now=now,
        )

    def _replay(self, existing: IngestionJob, candidate: IngestionJob) -> IngestionJob:
        if not existing.matches_idempotent_request(candidate):
            raise IdempotencyKeyReusedError("同一幂等键对应了不同的接入任务请求")
        return existing


def _uuid_ref(value: str | None, resource: str) -> UUID:
    if not value:
        raise ResourceNotFoundError(resource, value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError(resource, value) from exc


def _normalize_limit(limit: int) -> int:
    try:
        return normalize_limit(limit)
    except PaginationValidationError as exc:
        raise DomainValidationError(str(exc)) from exc


def _decode_cursor(cursor: str | None) -> UUID | None:
    try:
        return decode_cursor(cursor)
    except PaginationValidationError as exc:
        raise DomainValidationError(str(exc)) from exc


def _job_status(value: str | None) -> IngestionJobStatus | None:
    if value is None:
        return None
    try:
        return IngestionJobStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


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
