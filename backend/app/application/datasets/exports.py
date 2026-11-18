"""ExportJob 用例：创建、列表、详情、取消、重试、产物清单与下载。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import NewExportJob
from app.application.datasets.export_ports import (
    ExportJobRepository,
    ExportObjectStorage,
)
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetItemRepository,
    DatasetRepository,
    DatasetVersionRepository,
    IdempotencyRepository,
    OutboxEvent,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    DatasetStatus,
    DatasetVersionStatus,
    ExportJobStatus,
    ValidityStatus,
)
from app.domain.datasets.errors import (
    ArchiveBlockedError,
    DatasetVersionNotFrozenError,
    DomainValidationError,
    DuplicateResourceError,
    ExportChecksumMismatchError,
    ExportIncompleteError,
    ExportJobCancelledError,
    IdempotencyKeyReusedError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    UpstreamInvalidatedError,
    VersionConflictError,
)
from app.domain.datasets.export_jobs import (
    ExportJob,
    new_export_job,
)
from app.domain.datasets.models import DatasetVersion
from app.domain.datasets.state_machines import (
    allowed_export_actions,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

RESOURCE_TYPE = "EXPORT_JOB"
EXPORT_REQUESTED_EVENT = "dataset.export.requested"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与 ``apply_action`` 的显式白名单一致（worker 侧动作不走此路径）。
EXPORT_ACTION_EVENTS: dict[str, str] = {
    "cancel": "dataset.export.cancel",
    "retry": "dataset.export.retry",
}


class ExportJobUseCases:
    def __init__(
        self,
        *,
        datasets: DatasetRepository,
        versions: DatasetVersionRepository,
        items: DatasetItemRepository,
        jobs: ExportJobRepository,
        storage: ExportObjectStorage,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        output_prefix_root: str,
        default_timeout_seconds: int,
        max_timeout_seconds: int,
        default_max_attempts: int,
        max_attempts_limit: int,
        artifact_chunk_size_bytes: int,
        download_url_ttl_seconds: int,
    ) -> None:
        self._datasets = datasets
        self._versions = versions
        self._items = items
        self._jobs = jobs
        self._storage = storage
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._output_prefix_root = output_prefix_root.strip("/")
        self._default_timeout = default_timeout_seconds
        self._max_timeout = max_timeout_seconds
        self._default_max_attempts = default_max_attempts
        self._max_attempts_limit = max_attempts_limit
        self._chunk_size = artifact_chunk_size_bytes
        self._download_ttl = download_url_ttl_seconds

    # ---- 读取 ----
    def get(self, scope: ProjectScope, job_id: UUID) -> ExportJob:
        job = self._jobs.get(scope, job_id)
        if job is None:
            raise ResourceNotFoundError("导出任务", job_id)
        return job

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID | None = None,
        status: str | None = None,
        format: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExportJob]:
        return self._jobs.list(
            scope,
            dataset_version_id=dataset_version_id,
            status=status,
            format=format,
            after_id=after_id,
            limit=limit,
        )

    # ---- 创建 ----
    def create(
        self,
        scope: ProjectScope,
        command: NewExportJob,
        *,
        idempotency_key: str,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ExportJob:
        key = validate_key(idempotency_key)
        if key is None:
            raise DomainValidationError("创建导出任务必须提供 Idempotency-Key")
        now = _utcnow()
        timeout = (
            command.timeout_seconds
            if command.timeout_seconds is not None
            else self._default_timeout
        )
        if timeout > self._max_timeout:
            raise DomainValidationError(f"timeout_seconds 不得超过 {self._max_timeout}")
        attempts = (
            command.max_attempts if command.max_attempts is not None else self._default_max_attempts
        )
        if attempts > self._max_attempts_limit:
            raise DomainValidationError(f"max_attempts 不得超过 {self._max_attempts_limit}")
        job_id = uuid4()
        candidate = new_export_job(
            export_job_id=job_id,
            project_id=scope.project_id,
            dataset_version_id=command.dataset_version_id,
            format=command.format,
            format_version=command.format_version,
            exporter_id=command.exporter_id,
            exporter_version=command.exporter_version,
            mapping_version=command.mapping_version,
            config=dict(command.config),
            idempotency_key=key,
            requested_by=scope.actor_id,
            output_prefix=(
                f"{self._output_prefix_root}/{scope.project_id}/"
                f"{command.dataset_version_id}/{job_id}/"
            ),
            now=now,
            timeout_seconds=timeout,
            max_attempts=attempts,
        )
        existing = self._jobs.get_by_idempotency_key(scope, key)
        if existing is not None:
            return self._replay(existing, candidate)
        version = self._versions.get(scope, command.dataset_version_id)
        if version is None:
            raise ResourceNotFoundError("数据集版本", command.dataset_version_id)
        self._ensure_exportable(scope, version)
        try:
            created = self._jobs.add(scope, candidate)
        except DuplicateResourceError:
            existing = self._jobs.get_by_idempotency_key(scope, key)
            if existing is None:
                raise
            return self._replay(existing, candidate)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=RESOURCE_TYPE,
            resource_id=created.export_job_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "dataset_version_id": str(created.dataset_version_id),
                "format": created.format.value,
                "artifact_profile": "lerobot-metadata",
            },
            event_type="dataset.export.queued",
            event_payload={
                "export_job_id": str(created.export_job_id),
                "project_id": str(created.project_id),
                "dataset_version_id": str(created.dataset_version_id),
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._outbox.enqueue(
            scope,
            OutboxEvent(
                event_id=uuid4(),
                project_id=scope.project_id,
                aggregate_type=RESOURCE_TYPE,
                aggregate_id=created.export_job_id,
                event_type=EXPORT_REQUESTED_EVENT,
                payload={
                    "export_job_id": str(created.export_job_id),
                    # 供导出 Worker 恢复 trace_id；附加字段，语义不变。
                    **({"request_id": request_id} if request_id is not None else {}),
                },
                occurred_at=now,
                # 投递类：worker 要据此真的执行导出（R-13 / T-1）。
                dispatch=True,
            ),
        )
        self._uow.commit()
        return created

    def _replay(self, existing: ExportJob, candidate: ExportJob) -> ExportJob:
        if not existing.matches_idempotent_request(candidate):
            raise IdempotencyKeyReusedError("同一幂等键对应了不同的导出请求")
        return existing

    def _ensure_exportable(self, scope: ProjectScope, version: DatasetVersion) -> None:
        dataset = self._datasets.get(scope, version.dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", version.dataset_id)
        if dataset.status is DatasetStatus.ARCHIVED:
            raise ArchiveBlockedError("已归档数据集默认禁止新导出")
        if version.status is not DatasetVersionStatus.FROZEN:
            raise DatasetVersionNotFrozenError("只有 FROZEN 数据集版本可以导出")
        if version.validity_status is not ValidityStatus.VALID:
            raise UpstreamInvalidatedError("数据集版本有效性不是 VALID，禁止导出")

    # ---- 动作 ----
    def apply_action(
        self,
        scope: ProjectScope,
        job_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ExportJob:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="export")
        if action_name not in {"retry", "cancel"}:
            raise DomainValidationError("导出任务仅支持 retry/cancel 动作")
        body = {
            "action": action_name,
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
            return self.get(scope, _uuid_ref(replay.response_ref, "导出任务"))
        job = self.get(scope, job_id)
        if job.row_version != expected_version:
            raise VersionConflictError("导出任务")
        if job.status is ExportJobStatus.CANCELLED:
            raise ExportJobCancelledError("导出任务已取消，不能继续操作")
        if action_name not in allowed_export_actions(job.status):
            raise InvalidStateTransitionError("导出任务", job.status.value, action_name)
        if action_name == "cancel":
            if not reason or not reason.strip():
                raise DomainValidationError("取消导出任务必须填写原因")
            updated = job.cancel(now)
        elif action_name == "retry":
            updated = job.retry(now)
        else:
            raise InvalidStateTransitionError("导出任务", job.status.value, action_name)
        saved = self._jobs.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=RESOURCE_TYPE,
            resource_id=saved.export_job_id,
            action=action_name,
            from_status=job.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"attempt": saved.attempt, "max_attempts": saved.max_attempts},
            event_type=EXPORT_ACTION_EVENTS[action_name],
            event_payload={
                "export_job_id": str(saved.export_job_id),
                "from_status": job.status.value,
                "to_status": saved.status.value,
                "attempt": saved.attempt,
            },
            occurred_at=now,
        )
        if action_name == "retry":
            self._outbox.enqueue(
                scope,
                OutboxEvent(
                    event_id=uuid4(),
                    project_id=scope.project_id,
                    aggregate_type=RESOURCE_TYPE,
                    aggregate_id=saved.export_job_id,
                    event_type=EXPORT_REQUESTED_EVENT,
                    payload={"export_job_id": str(saved.export_job_id)},
                    occurred_at=now,
                    dispatch=True,
                ),
            )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=202 if action_name == "retry" else 200,
            response_ref=str(saved.export_job_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # ---- 产物 ----
    def artifact_manifest(self, scope: ProjectScope, job_id: UUID) -> dict[str, object]:
        job = self.get(scope, job_id)
        if job.status is not ExportJobStatus.SUCCEEDED:
            raise ExportIncompleteError("导出任务未成功，产物清单不可用")
        if not job.artifact_manifest_key:
            raise ExportIncompleteError("导出任务缺少产物清单")
        data = self._read(job.artifact_manifest_key)
        if sha256_text(data.decode("utf-8")) != job.artifact_manifest_sha256:
            raise ExportChecksumMismatchError("产物清单 Hash 校验失败")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportChecksumMismatchError("产物清单内容非法") from exc
        if not isinstance(payload, dict):
            raise ExportChecksumMismatchError("产物清单结构非法")
        return payload

    def artifact_download(
        self,
        scope: ProjectScope,
        job_id: UUID,
        artifact_path: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, object]:
        job = self.get(scope, job_id)
        manifest = self.artifact_manifest(scope, job_id)
        normalized = _safe_artifact_path(artifact_path)
        entry = next(
            (
                item
                for item in manifest.get("artifacts", [])
                if isinstance(item, dict) and item.get("path") == normalized
            ),
            None,
        )
        if entry is None:
            raise ResourceNotFoundError("导出产物", normalized)
        object_key = f"{job.output_prefix}{normalized}"
        presigned = self._storage.presign_download(
            object_key,
            expires_in_seconds=self._download_ttl,
        )
        now = _utcnow()
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="EXPORT_ARTIFACT",
            resource_id=job.export_job_id,
            action="download",
            from_status=job.status.value,
            to_status=job.status.value,
            reason=None,
            request_id=request_id,
            details={"path": normalized},
            event_type="dataset.export.artifact.downloaded",
            event_payload={
                "export_job_id": str(job.export_job_id),
                "path": normalized,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return {
            "path": normalized,
            "sha256": entry.get("sha256"),
            "size_bytes": entry.get("size_bytes"),
            "download_url": presigned.url,
            "download_url_expires_at": presigned.expires_at.isoformat(),
        }

    def _read(self, object_key: str) -> bytes:
        return b"".join(self._storage.stream_object(object_key, chunk_size=self._chunk_size))


def _safe_artifact_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResourceNotFoundError("导出产物", value)
    normalized = value.strip()
    if (
        normalized.startswith("/")
        or ".." in normalized.split("/")
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise ResourceNotFoundError("导出产物", value)
    return normalized


def _uuid_ref(value: str | None, resource: str) -> UUID:
    if not value:
        raise ResourceNotFoundError(resource, value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError(resource, value) from exc


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EXPORT_REQUESTED_EVENT",
    "ExportJobUseCases",
]
