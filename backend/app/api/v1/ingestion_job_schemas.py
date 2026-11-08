"""IngestionJob HTTP DTO。

请求模型统一 ``extra="forbid"``；客户端不能提交 project_id、status、attempt、
version、审计字段或时间戳等权威字段。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.ingestion.jobs import IngestionJob

_JOB_TYPE_PATTERN = "^(VALIDATE|PARSE_METADATA|SEGMENT_EPISODE|REVALIDATE)$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class IngestionJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: str = Field(pattern=_JOB_TYPE_PATTERN)
    timeout_sec: int = Field(gt=0, le=86400)
    raw_asset_ids: list[UUID] = Field(default_factory=list, max_length=64)
    input_manifest_key: str | None = Field(default=None, max_length=1024)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=100)
    pipeline_id: str | None = Field(default=None, max_length=128)
    pipeline_version: str | None = Field(default=None, max_length=64)
    parser_id: str | None = Field(default=None, max_length=128)
    parser_version: str | None = Field(default=None, max_length=64)
    config_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    metrics: dict[str, object] = Field(default_factory=dict)


class IngestionJobActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    project_id: UUID
    job_type: str
    raw_asset_ids: list[UUID]
    input_manifest_key: str | None
    idempotency_key: str | None
    status: str
    attempt: int
    max_attempts: int
    timeout_sec: int
    pipeline_id: str | None
    pipeline_version: str | None
    parser_id: str | None
    parser_version: str | None
    config_hash: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_summary: str | None
    output_refs: list[dict[str, object]]
    log_ref: str | None
    metrics: dict[str, object]
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, job: IngestionJob) -> IngestionJobOut:
        return cls(
            job_id=job.job_id,
            project_id=job.project_id,
            job_type=_value(job.job_type),
            raw_asset_ids=list(job.raw_asset_ids),
            input_manifest_key=job.input_manifest_key,
            idempotency_key=job.idempotency_key,
            status=_value(job.status),
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            timeout_sec=job.timeout_sec,
            pipeline_id=job.pipeline_id,
            pipeline_version=job.pipeline_version,
            parser_id=job.parser_id,
            parser_version=job.parser_version,
            config_hash=job.config_hash,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=job.error_code,
            error_summary=job.error_summary,
            output_refs=[dict(item) for item in job.output_refs],
            log_ref=job.log_ref,
            metrics=dict(job.metrics),
            version=job.version,
            created_at=job.created_at,
            updated_at=job.updated_at,
            created_by=job.created_by,
            updated_by=job.updated_by,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
