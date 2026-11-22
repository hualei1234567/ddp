"""ExportJob Worker 执行服务。

只在 Worker 进程内调用：生成确定性元数据产物、写入对象存储、回读校验 Hash、
生成 artifact manifest，并在同一数据库事务内写状态、审计与 outbox 事件。
HTTP 线程不执行任何产物读写。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
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
from app.application.datasets.quality_gate import (
    EpisodeQualityGate,
    export_gate_failure_summary,
)
from app.core.logging import current_trace_id
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    DatasetStatus,
    DatasetVersionStatus,
    ExportJobStatus,
)
from app.domain.datasets.errors import (
    ArchiveBlockedError,
    DatasetVersionNotFrozenError,
    ExportArtifactVerifyFailedError,
    ExportOutputConflictError,
    QualityGateFailedError,
    ResourceNotFoundError,
    UpstreamInvalidatedError,
)
from app.domain.datasets.export_jobs import ExportJob
from app.domain.datasets.exporting import (
    EpisodeExportSource,
    ExportArtifactSpec,
    aggregate_sha256,
    build_artifact_manifest,
    build_export_artifacts,
    manifest_bytes,
)
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.selection import SelectionFilters
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.errors import ObjectNotFoundError, ObjectStorageUnavailableError

RESOURCE_TYPE = "EXPORT_JOB"


class ExportEpisodeSourceDirectory(Protocol):
    """导出所需的 Episode 事实、已批准标签与媒体位置读取端口。

    与 ``ExportJobRepository`` 等端口分开放：它跨 episodes / annotation_records /
    episode_raw_assets / raw_assets 多个模块取数，但不写任何数据，也不承担业务
    规则——只把"导出要什么"翻译成一次读操作。实现必须在服务端强制
    ``scope.project_id`` 过滤，不信任客户端传入的项目标识。
    """

    def list_for_items(
        self,
        scope: ProjectScope,
        items: Sequence[DatasetItem],
    ) -> Sequence[EpisodeExportSource]: ...


class ExportExecutionService:
    def __init__(
        self,
        *,
        datasets: DatasetRepository,
        versions: DatasetVersionRepository,
        items: DatasetItemRepository,
        jobs: ExportJobRepository,
        storage: ExportObjectStorage,
        episode_sources: ExportEpisodeSourceDirectory,
        quality_gate: EpisodeQualityGate,
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
        self._episode_sources = episode_sources
        self._quality_gate = quality_gate
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
        gate_message = export_gate_failure_summary(
            self._quality_gate,
            scope,
            SelectionFilters.from_mapping(dict(version.selection_filters)),
            items,
        )
        if gate_message is not None:
            return self._fail(
                scope,
                job,
                moment,
                QualityGateFailedError.code,
                gate_message,
            )
        created_at = version.frozen_at or version.source_snapshot_at or moment
        # 媒体位置与已批准标签只在 Worker 内读一次；读失败按任务失败处理，
        # 绝不降级成"空标签也当成功"——那会把缺数据伪装成合法训练集。
        sources = tuple(self._episode_sources.list_for_items(scope, tuple(items)))
        try:
            artifacts = build_export_artifacts(
                job,
                version,
                items,
                created_at=created_at,
                sources=sources,
            )
        except ValueError as exc:
            return self._fail(scope, job, moment, "export_job_failed", str(exc))

        written = 0
        total_bytes = 0
        # 契约：「输出路径/产物冲突；不得静默覆盖成功产物」。retry 会复用同一个
        # output_prefix（见 ExportJob.retry），因此重跑时前缀下可能已有上次成功
        # 或部分写出的对象。这里先探测，一旦发现已存在对象就确定性失败，
        # 绝不覆盖——覆盖会让历史产物的 Hash 与实际内容脱钩。
        conflict = self._detect_output_conflict(job, artifacts)
        if conflict is not None:
            return self._fail(
                scope,
                job,
                moment,
                ExportOutputConflictError.code,
                f"输出路径已存在产物，拒绝覆盖：{conflict}",
            )
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
        blocked = version.validity_block_reason
        if blocked is not None:
            self._fail(
                scope,
                job,
                moment,
                UpstreamInvalidatedError.code,
                f"{blocked}，禁止导出",
            )
            return None
        return version

    def _detect_output_conflict(
        self,
        job: ExportJob,
        artifacts: tuple[ExportArtifactSpec, ...],
    ) -> str | None:
        """探测目标前缀下是否已有对象；返回首个冲突对象键，无冲突返回 None。

        只探测不写入、不删除：发现冲突就交给状态机记 FAILED，由人决定是否
        显式接管旧产物，避免"自动清理"变成隐式数据丢失。
        """
        for artifact in artifacts:
            object_key = f"{job.output_prefix}{artifact.path}"
            if self._storage.object_size(object_key) is not None:
                return object_key
        return None

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
        # V31-042：产物含二进制 parquet，统一按字节哈希校验——与
        # ExportArtifactSpec.sha256（bytes 直接哈希）同口径；utf-8 文本的
        # 字节哈希与原 sha256_text(text) 等价，既有产物校验不变。
        if (
            len(readback) != artifact.size_bytes
            or "sha256:" + hashlib.sha256(readback).hexdigest() != artifact.sha256
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
            # 本服务只在 Worker 进程内调用；上下文由 outbox 事件的
            # request_id 恢复（缺失时由 Worker 生成并标注 generated），
            # 这里沿用同一 trace_id，保证「请求 → 事件 → 审计」可串起来。
            request_id=current_trace_id(),
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


__all__ = ["ExportEpisodeSourceDirectory", "ExportExecutionService"]
