"""ExportJob Worker 执行服务。

只在 Worker 进程内调用：生成确定性元数据产物、写入对象存储、回读校验 Hash、
生成 artifact manifest，并在同一数据库事务内写状态、审计与 outbox 事件。
HTTP 线程不执行任何产物读写。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.datasets.audit import record_change
from app.application.datasets.export_ports import (
    ExportJobRepository,
    ExportObjectStorage,
)
from app.application.datasets.ports import (
    AuditRepository,
    DatasetItemRepository,
    DatasetRepository,
    DatasetVersionRepository,
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
    ExportArtifactVerifyFailedError,
    ResourceNotFoundError,
    UpstreamInvalidatedError,
)
from app.domain.datasets.export_jobs import ExportJob
from app.domain.datasets.exporting import (
    ExportArtifactSpec,
    aggregate_sha256,
    build_artifact_manifest,
    build_export_artifacts,
    manifest_bytes,
)
from app.domain.datasets.models import DatasetVersion
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.errors import ObjectNotFoundError, ObjectStorageUnavailableError

RESOURCE_TYPE = "EXPORT_JOB"


class ExportExecutionService:
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
        uow: UnitOfWork,
        artifact_chunk_size_bytes: int,
    ) -> None:
        self._datasets = datasets
        self._versions = versions
        self._items = items
        self._jobs = jobs
        self._storage = storage
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._chunk_size = artifact_chunk_size_bytes

    def execute(
        self,
        scope: ProjectScope,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ExportJob:
        moment = now or _utcnow()
        job = self._load(scope, job_id)
        if job.status in {
            ExportJobStatus.SUCCEEDED,
            ExportJobStatus.PARTIAL,
            ExportJobStatus.CANCELLED,
        }:
            return job
        if job.status is ExportJobStatus.FAILED:
            # 未经过显式 retry 的 FAILED 任务不得被 Worker 直接重跑。
            return job
        if job.status is ExportJobStatus.QUEUED:
            started = job.start(moment)
            job = self._jobs.save(scope, started, expected_version=job.row_version)
            self._record(scope, job, moment, "worker-start", "dataset.export.started")
        job = self._load(scope, job_id)
        if job.status is ExportJobStatus.CANCELLED:
            return job
        deadline = job.timeout_at
        if job.status is ExportJobStatus.RUNNING and deadline is not None and moment >= deadline:
            return self._fail_timeout(scope, job, moment, written=0)

        version = self._load_exportable_version(scope, job, moment)
        if version is None:
            return self._load(scope, job_id)
        items = self._items.list_all(scope, job.dataset_version_id)
        if not items:
            return self._fail(
                scope,
                job,
                moment,
                "export_job_failed",
                "数据集版本没有可导出的条目",
            )
        created_at = version.frozen_at or version.source_snapshot_at or moment
        try:
            artifacts = build_export_artifacts(
                job,
                version,
                items,
                created_at=created_at,
            )
        except ValueError as exc:
            return self._fail(scope, job, moment, "export_job_failed", str(exc))

        written = 0
        total_bytes = 0
        for artifact in artifacts:
            job = self._load(scope, job_id)
            if job.status is ExportJobStatus.CANCELLED:
                return job
            if deadline is not None and moment >= deadline:
                if written:
                    return self._partial_timeout(scope, job, moment, written, total_bytes)
                return self._fail_timeout(scope, job, moment, written=0)
            try:
                self._write_and_verify(job, artifact)
            except ExportArtifactVerifyFailedError:
                return self._partial(
                    scope,
                    job,
                    moment,
                    "export_artifact_verify_failed",
                    f"产物回读校验失败：{artifact.path}",
                    written + 1,
                    total_bytes + artifact.size_bytes,
                )
            except (ObjectStorageUnavailableError, ObjectNotFoundError):
                if written:
                    return self._partial(
                        scope,
                        job,
                        moment,
                        "export_partial",
                        f"部分产物写入失败：{artifact.path}",
                        written,
                        total_bytes,
                    )
                return self._fail(
                    scope,
                    job,
                    moment,
                    "export_job_failed",
                    f"产物写入失败：{artifact.path}",
                )
            written += 1
            total_bytes += artifact.size_bytes

        aggregate = aggregate_sha256(artifacts)
        manifest = build_artifact_manifest(job, artifacts, aggregate=aggregate)
        manifest_data = manifest_bytes(manifest)
        manifest_key = f"{job.output_prefix}_artifact_manifest.json"
        manifest_artifact = ExportArtifactSpec(
            path="_artifact_manifest.json",
            data=manifest_data,
            content_type="application/json",
        )
        job = self._load(scope, job_id)
        if job.status is ExportJobStatus.CANCELLED:
            return job
        try:
            self._write_and_verify(job, manifest_artifact, object_key_override=manifest_key)
        except ExportArtifactVerifyFailedError:
            return self._partial(
                scope,
                job,
                moment,
                "export_artifact_verify_failed",
                "artifact manifest 回读校验失败",
                written,
                total_bytes,
            )
        except (ObjectStorageUnavailableError, ObjectNotFoundError):
            return self._partial(
                scope,
                job,
                moment,
                "export_partial",
                "artifact manifest 写入失败",
                written,
                total_bytes,
            )

        job = self._load(scope, job_id)
        if job.status is ExportJobStatus.CANCELLED:
            return job
        if deadline is not None and moment >= deadline:
            return self._partial_timeout(scope, job, moment, written, total_bytes)
        succeeded = job.succeed(
            now=moment,
            artifact_count=written,
            total_bytes=total_bytes,
            artifact_manifest_key=manifest_key,
            artifact_manifest_sha256=sha256_text(manifest_data.decode("utf-8")),
            aggregate_sha256=aggregate,
            log_ref=f"export:{job.export_job_id}",
        )
        saved = self._jobs.save(scope, succeeded, expected_version=job.row_version)
        self._record(scope, saved, moment, "worker-succeed", "dataset.export.succeeded")
        self._uow.commit()
        return saved

    # ---- 内部 ----
    def _load(self, scope: ProjectScope, job_id: UUID) -> ExportJob:
        job = self._jobs.get(scope, job_id)
        if job is None:
            raise ResourceNotFoundError("导出任务", job_id)
        return job

    def _load_exportable_version(
        self,
        scope: ProjectScope,
        job: ExportJob,
        moment: datetime,
    ) -> DatasetVersion | None:
        version = self._versions.get(scope, job.dataset_version_id)
        if version is None:
            self._fail(scope, job, moment, "export_job_failed", "数据集版本不存在")
            return None
        dataset = self._datasets.get(scope, version.dataset_id)
        if dataset is None:
            self._fail(scope, job, moment, "export_job_failed", "数据集不存在")
            return None
        if dataset.status is DatasetStatus.ARCHIVED:
            self._fail(scope, job, moment, ArchiveBlockedError.code, "数据集已归档，禁止导出")
            return None
        if version.status is not DatasetVersionStatus.FROZEN:
            self._fail(
                scope,
                job,
                moment,
                DatasetVersionNotFrozenError.code,
                "数据集版本不再是 FROZEN，禁止导出",
            )
            return None
        if version.validity_status is not ValidityStatus.VALID:
            self._fail(
                scope,
                job,
                moment,
                UpstreamInvalidatedError.code,
                "数据集版本有效性已失效，禁止导出",
            )
            return None
        return version

    def _write_and_verify(
        self,
        job: ExportJob,
        artifact: ExportArtifactSpec,
        *,
        object_key_override: str | None = None,
    ) -> None:
        object_key = object_key_override or f"{job.output_prefix}{artifact.path}"
        self._storage.put_bytes(
            object_key,
            artifact.data,
            content_type=artifact.content_type,
        )
        readback = b"".join(self._storage.stream_object(object_key, chunk_size=self._chunk_size))
        if (
            len(readback) != artifact.size_bytes
            or sha256_text(readback.decode("utf-8")) != artifact.sha256
        ):
            raise ExportArtifactVerifyFailedError(f"产物校验失败：{artifact.path}")

    def _fail(
        self,
        scope: ProjectScope,
        job: ExportJob,
        now: datetime,
        error_code: str,
        error_summary: str,
    ) -> ExportJob:
        current = self._load(scope, job.export_job_id)
        if current.status is not ExportJobStatus.RUNNING:
            return current
        failed = current.fail(
            now=now,
            error_code=error_code,
            error_summary=error_summary,
            log_ref=f"export:{current.export_job_id}",
        )
        saved = self._jobs.save(scope, failed, expected_version=current.row_version)
        self._record(scope, saved, now, "worker-fail", "dataset.export.failed")
        self._uow.commit()
        return saved

    def _partial(
        self,
        scope: ProjectScope,
        job: ExportJob,
        now: datetime,
        error_code: str,
        error_summary: str,
        written: int,
        total_bytes: int,
    ) -> ExportJob:
        current = self._load(scope, job.export_job_id)
        if current.status is not ExportJobStatus.RUNNING:
            return current
        partial = current.partial(
            now=now,
            error_code=error_code,
            error_summary=error_summary,
            artifact_count=written,
            total_bytes=total_bytes,
            log_ref=f"export:{current.export_job_id}",
        )
        saved = self._jobs.save(scope, partial, expected_version=current.row_version)
        self._record(scope, saved, now, "worker-partial", "dataset.export.partial")
        self._uow.commit()
        return saved

    def _fail_timeout(
        self,
        scope: ProjectScope,
        job: ExportJob,
        now: datetime,
        *,
        written: int,
    ) -> ExportJob:
        current = self._load(scope, job.export_job_id)
        if current.status is not ExportJobStatus.RUNNING:
            return current
        timed_out = current.fail_timeout(now)
        timed_out = replace(
            timed_out,
            artifact_count=written if written else timed_out.artifact_count,
        )
        saved = self._jobs.save(scope, timed_out, expected_version=current.row_version)
        self._record(scope, saved, now, "job-timeout", "dataset.export.failed")
        self._uow.commit()
        return saved

    def _partial_timeout(
        self,
        scope: ProjectScope,
        job: ExportJob,
        now: datetime,
        written: int,
        total_bytes: int,
    ) -> ExportJob:
        return self._partial(
            scope,
            job,
            now,
            "export_job_timeout",
            "导出任务超时，已写出的部分产物不可作为成功结果",
            written,
            total_bytes,
        )

    def _record(
        self,
        scope: ProjectScope,
        job: ExportJob,
        now: datetime,
        action: str,
        event_type: str,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=RESOURCE_TYPE,
            resource_id=job.export_job_id,
            action=action,
            from_status=None,
            to_status=job.status.value,
            reason=job.error_summary,
            request_id=None,
            details={
                "attempt": job.attempt,
                "artifact_count": job.artifact_count,
                "total_bytes": job.total_bytes,
                "error_code": job.error_code,
            },
            event_type=event_type,
            event_payload={
                "export_job_id": str(job.export_job_id),
                "status": job.status.value,
                "attempt": job.attempt,
                "aggregate_sha256": job.aggregate_sha256,
            },
            occurred_at=now,
        )
        # outbox 事件 ID 由 record_change 生成；此处补充一个显式请求事件不必要，
        # Worker 自身不再入队新任务，重试只能经 HTTP 动作触发。


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["ExportExecutionService"]
