"""RawAsset 异步校验用例：流式重算 Hash/大小并迁移状态。

事务边界由调用方（Worker）持有：
- 业务状态、审计与 ``ingestion.raw_asset.validated`` 事件同事务写入；
- 审计或 outbox 写入失败会向上抛出，由 Worker 回滚整个事务；
- 已完成的资产直接返回 ``SKIPPED``，重复事件不会再次迁移。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    ConflictError,
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
    #: 内容与已存在的活跃资产完全相同：本资产被墓碑化并指向已有资产。
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    asset_id: UUID
    outcome: ValidationOutcome
    from_status: str
    to_status: str
    quarantine_codes: tuple[str, ...] = ()
    version: int | None = None
    #: ``DUPLICATE`` 时指向保留下来的活跃资产。
    duplicate_of_asset_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _AppliedDecision:
    """``_apply_decision`` 的返回：落库后的资产 + 可选的「已存在同内容资产」指针。"""

    asset: RawAsset
    duplicate_of_asset_id: UUID | None = None


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

        applied = self._apply_decision(scope, asset, decision, now=occurred_at)
        saved = applied.asset
        self._record(
            scope,
            saved,
            decision=decision,
            digest=digest,
            validation_job_id=validation_job_id,
            request_id=request_id,
            now=occurred_at,
        )
        if applied.duplicate_of_asset_id is not None:
            return ValidationResult(
                asset_id=saved.asset_id,
                outcome=ValidationOutcome.DUPLICATE,
                from_status=from_status,
                to_status=saved.status.value,
                quarantine_codes=saved.quarantine_codes,
                version=saved.version,
                duplicate_of_asset_id=applied.duplicate_of_asset_id,
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
    ) -> _AppliedDecision:
        """落库校验结论；内容与已有活跃资产冲突时降级为重复而非失败。

        为什么需要这条降级
        ------------------
        ``uq_raw_assets_project_content_active`` 保证「同项目相同内容的活跃资产
        只有一个」。但内容去重在上传完成时按 ``(sha256, size_bytes)`` 查一次，
        两个请求**并发**完成上传时可以双双查不到对方，于是各自留下一个
        VALIDATING 资产；等到校验转 VALID 时第二个必然撞索引。

        这曾表现为 outbox 事件永久 FAILED（``UniqueViolation``），把「这次上传
        的内容库里已经有一份了」误报成「校验失败」。正确处理是把它识别为重复：
        保留先到的那份活跃资产，本次资产墓碑化并指向它，下载继续走已有资产。

        冲突判定放在这里而不是仓储层：仓储只负责把 IntegrityError 翻成
        ConflictError，**语义**（这是重复、该指向谁）属于用例。
        """
        if decision.valid:
            updated = asset.mark_valid(now=now)
        else:
            updated = asset.mark_quarantined(
                codes=decision.codes,
                reason=decision.reason or "内容校验未通过",
                now=now,
            )
        try:
            return _AppliedDecision(
                asset=self._assets.save(scope, updated, expected_version=asset.version),
                duplicate_of_asset_id=None,
            )
        except ConflictError:
            # 已有活跃资产占着这份内容：把本次资产墓碑化并指过去。
            existing = self._assets.find_active_by_content(
                scope, sha256=asset.sha256, size_bytes=asset.size_bytes
            )
            if existing is None or existing.asset_id == asset.asset_id:
                # 查不到对端（例如并发方随后又变更状态）：仍然失败，
                # 不静默吞掉——"内容冲突却说不出是谁"必须暴露给人看。
                raise
            tombstoned = self._assets.save(
                scope,
                replace(
                    asset.apply_action(
                        "tombstone",
                        {"reason": "内容与已有资产重复，已墓碑化并指向该资产"},
                        now=now,
                    ),
                    duplicate_of_asset_id=existing.asset_id,
                ),
                expected_version=asset.version,
            )
            return _AppliedDecision(asset=tombstoned, duplicate_of_asset_id=existing.asset_id)

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
        if request_id is not None:
            # 后续同链路事件沿用同一 trace_id；附加字段，既有字段语义不变。
            payload["request_id"] = request_id
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
