"""RawAsset 异步校验用例：流式重算 Hash/大小并迁移状态。

事务边界由调用方（Worker）持有：
- 业务状态、审计与 ``ingestion.raw_asset.validated`` 事件同事务写入；
- 审计或 outbox 写入失败会向上抛出，由 Worker 回滚整个事务；
- 已完成的资产直接返回 ``SKIPPED``，重复事件不会再次迁移。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.application.collection.ports import (
    AuditRecord,
    AuditRepository,
    OutboxEvent,
    OutboxRepository,
)
from app.application.ingestion.ports import ObjectStorage, RawAssetRepository
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.content_validation import (
    ContentDigest,
    ValidationDecision,
    evaluate_content,
    object_missing_decision,
)
from app.domain.ingestion.enums import RawAssetStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    ObjectNotFoundError,
    ResourceNotFoundError,
)
from app.domain.ingestion.models import RawAsset

VALIDATION_EVENT_TYPE = "ingestion.raw_asset.validation_requested"
VALIDATED_EVENT_TYPE = "ingestion.raw_asset.validated"
RAW_ASSET_RESOURCE_TYPE = "RAW_ASSET"


class ValidationOutcome(StrEnum):
    VALID = "VALID"
    QUARANTINED = "QUARANTINED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    asset_id: UUID
    outcome: ValidationOutcome
    from_status: str
    to_status: str
    quarantine_codes: tuple[str, ...] = ()
    version: int | None = None


class RawAssetValidationService:
    def __init__(
        self,
        assets: RawAssetRepository,
        storage: ObjectStorage,
        audit: AuditRepository,
        outbox: OutboxRepository,
    ) -> None:
        self._assets = assets
        self._storage = storage
        self._audit = audit
        self._outbox = outbox

    def validate(
        self,
        *,
        project_id: UUID,
        asset_id: UUID,
        actor_user_id: UUID | None,
        validation_job_id: UUID | None,
        request_id: str | None,
        chunk_size_bytes: int,
        now: datetime | None = None,
    ) -> ValidationResult:
        if chunk_size_bytes <= 0:
            raise DomainValidationError("chunk_size_bytes 必须是正整数")
        occurred_at = now or datetime.now(UTC)
        # 读取阶段用事件项目作为占位 actor；写审计时再替换为真实主体。
        read_scope = ProjectScope(project_id=project_id, actor_id=actor_user_id or project_id)
        asset = self._assets.get_for_update(read_scope, asset_id)
        if asset is None:
            # 跨项目资产同样不可见，避免事件携带错误 project_id 时越权迁移。
            raise ResourceNotFoundError("原始资产", asset_id)

        from_status = asset.status.value
        if asset.status is not RawAssetStatus.VALIDATING:
            return ValidationResult(
                asset_id=asset.asset_id,
                outcome=ValidationOutcome.SKIPPED,
                from_status=from_status,
                to_status=from_status,
                quarantine_codes=asset.quarantine_codes,
                version=asset.version,
            )

        scope = ProjectScope(
            project_id=project_id,
            actor_id=actor_user_id or asset.uploaded_by,
        )
        digest: ContentDigest | None
        try:
            digest = _hash_stream(
                self._storage.stream_object(
                    asset.object_key,
                    chunk_size=chunk_size_bytes,
                )
            )
        except ObjectNotFoundError:
            digest = None
            decision = object_missing_decision()
        else:
            decision = evaluate_content(
                expected_sha256=asset.sha256,
                expected_size_bytes=asset.size_bytes,
                actual=digest,
            )

        saved = self._apply_decision(scope, asset, decision, now=occurred_at)
        self._record(
            scope,
            saved,
            decision=decision,
            digest=digest,
            validation_job_id=validation_job_id,
            request_id=request_id,
            now=occurred_at,
        )
        return ValidationResult(
            asset_id=saved.asset_id,
            outcome=(ValidationOutcome.VALID if decision.valid else ValidationOutcome.QUARANTINED),
            from_status=from_status,
            to_status=saved.status.value,
            quarantine_codes=saved.quarantine_codes,
            version=saved.version,
        )

    def _apply_decision(
        self,
        scope: ProjectScope,
        asset: RawAsset,
        decision: ValidationDecision,
        *,
        now: datetime,
    ) -> RawAsset:
        if decision.valid:
            updated = asset.mark_valid(now=now)
        else:
            updated = asset.mark_quarantined(
                codes=decision.codes,
                reason=decision.reason or "内容校验未通过",
                now=now,
            )
        return self._assets.save(scope, updated, expected_version=asset.version)

    def _record(
        self,
        scope: ProjectScope,
        asset: RawAsset,
        *,
        decision: ValidationDecision,
        digest: ContentDigest | None,
        validation_job_id: UUID | None,
        request_id: str | None,
        now: datetime,
    ) -> None:
        details: dict[str, object] = {
            "asset_id": str(asset.asset_id),
            "outcome": (
                ValidationOutcome.VALID.value
                if decision.valid
                else ValidationOutcome.QUARANTINED.value
            ),
            "expected_sha256": asset.sha256,
            "expected_size_bytes": asset.size_bytes,
            "quarantine_codes": [code.value for code in decision.codes],
        }
        if digest is not None:
            details["actual_sha256"] = digest.sha256
            details["actual_size_bytes"] = digest.size_bytes
        if validation_job_id is not None:
            details["validation_job_id"] = str(validation_job_id)

        self._audit.record(
            scope,
            AuditRecord(
                audit_id=uuid4(),
                project_id=scope.project_id,
                actor_user_id=scope.actor_id,
                actor_role=None,
                resource_type=RAW_ASSET_RESOURCE_TYPE,
                resource_id=asset.asset_id,
                action="validate",
                from_status=RawAssetStatus.VALIDATING.value,
                to_status=asset.status.value,
                reason=decision.reason,
                request_id=request_id,
                result="SUCCESS" if decision.valid else "FAILURE",
                details=details,
                occurred_at=now,
            ),
        )
        payload: dict[str, object] = {
            "asset_id": str(asset.asset_id),
            "status": asset.status.value,
            "outcome": details["outcome"],
            "quarantine_codes": [code.value for code in decision.codes],
            "version": asset.version,
        }
        if validation_job_id is not None:
            payload["validation_job_id"] = str(validation_job_id)
        self._outbox.enqueue(
            scope,
            OutboxEvent(
                event_id=uuid4(),
                project_id=scope.project_id,
                aggregate_type=RAW_ASSET_RESOURCE_TYPE,
                aggregate_id=asset.asset_id,
                event_type=VALIDATED_EVENT_TYPE,
                payload=payload,
                occurred_at=now,
            ),
        )


def _hash_stream(chunks: Iterable[bytes]) -> ContentDigest:
    hasher = hashlib.sha256()
    total = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise DomainValidationError("对象存储返回了非字节块")
        data = bytes(chunk)
        hasher.update(data)
        total += len(data)
    return ContentDigest(sha256=f"sha256:{hasher.hexdigest()}", size_bytes=total)
