"""IngestionJob 用例命令。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CreateIngestionJobCommand:
    job_type: str
    timeout_sec: int
    raw_asset_ids: tuple[UUID, ...] = ()
    input_manifest_key: str | None = None
    idempotency_key: str | None = None
    max_attempts: int = 3
    pipeline_id: str | None = None
    pipeline_version: str | None = None
    parser_id: str | None = None
    parser_version: str | None = None
    config_hash: str | None = None
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JobActionCommand:
    action: str
    expected_version: int
    reason: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    output_refs: Sequence[Mapping[str, object]] = ()
    log_ref: str | None = None
    metrics: Mapping[str, object] = field(default_factory=dict)
