"""ExportJob HTTP 请求模型与响应序列化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.datasets.export_jobs import ExportJob


class ExportJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version_id: UUID
    format: str = Field(min_length=1, max_length=32)
    format_version: str = Field(min_length=1, max_length=64)
    exporter_id: str = Field(min_length=1, max_length=100)
    exporter_version: str = Field(min_length=1, max_length=100)
    mapping_version: str = Field(min_length=1, max_length=100)
    config: dict[str, object] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)


class ExportActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def export_job_out(job: ExportJob) -> dict[str, Any]:
    return {
        "export_job_id": job.export_job_id,
        "project_id": job.project_id,
        "dataset_version_id": job.dataset_version_id,
        "status": _value(job.status),
        "format": _value(job.format),
        "format_version": job.format_version,
        "exporter_id": job.exporter_id,
        "exporter_version": job.exporter_version,
        "mapping_version": job.mapping_version,
        "config": job.config,
        "config_sha256": job.config_sha256,
        "idempotency_key": job.idempotency_key,
        "requested_by": job.requested_by,
        "output_prefix": job.output_prefix,
        "artifact_count": job.artifact_count,
        "total_bytes": job.total_bytes,
        "artifact_manifest_key": job.artifact_manifest_key,
        "artifact_manifest_sha256": job.artifact_manifest_sha256,
        "aggregate_sha256": job.aggregate_sha256,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "timeout_seconds": job.timeout_seconds,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "cancel_requested_at": _iso(job.cancel_requested_at),
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "log_ref": job.log_ref,
        "verification_status": _value(job.verification_status),
        "verification_evidence_ref": job.verification_evidence_ref,
        "loadable_scope": _value(job.loadable_scope),
        "allowed_actions": sorted(job.allowed_actions),
        "version": job.row_version,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
    }
