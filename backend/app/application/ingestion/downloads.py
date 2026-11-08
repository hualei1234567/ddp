"""RawAsset 下载地址用例。

HTTP 线程只生成短时预签名 GET URL，不读取对象内容；对象下载由调用方直接
访问对象存储。URL 属于敏感凭据，审计只记录资产、有效期与请求上下文，
绝不写入 URL 明文或对象存储签名参数。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.ports import AuditRecord, AuditRepository
from app.application.ingestion.ports import (
    ObjectStorage,
    PresignedDownload,
    RawAssetRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetStatus
from app.domain.ingestion.errors import AssetNotUsableError, ResourceNotFoundError


class RawAssetDownloadUseCases:
    def __init__(
        self,
        assets: RawAssetRepository,
        storage: ObjectStorage,
        audit: AuditRepository,
        uow: UnitOfWork,
        *,
        url_ttl_seconds: int,
    ) -> None:
        if url_ttl_seconds <= 0:
            raise ValueError("下载 URL 有效期必须为正数")
        self._assets = assets
        self._storage = storage
        self._audit = audit
        self._uow = uow
        self._url_ttl_seconds = url_ttl_seconds

    def create_download_url(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        *,
        request_id: str | None = None,
    ) -> PresignedDownload:
        asset = self._assets.get(scope, asset_id)
        if asset is None:
            raise ResourceNotFoundError("原始资产", asset_id)
        if asset.status not in {RawAssetStatus.VALID, RawAssetStatus.ARCHIVED}:
            raise AssetNotUsableError("资产未通过校验或已失效，不可下载")
        signed = self._storage.presign_download(
            asset.object_key,
            expires_in_seconds=self._url_ttl_seconds,
        )
        now = datetime.now(UTC)
        self._audit.record(
            scope,
            AuditRecord(
                audit_id=uuid4(),
                project_id=scope.project_id,
                actor_user_id=scope.actor_id,
                actor_role=None,
                resource_type="RAW_ASSET",
                resource_id=asset.asset_id,
                action="download_requested",
                from_status=None,
                to_status=None,
                reason=None,
                request_id=request_id,
                result="SUCCESS",
                # 禁止写入 URL、签名参数或对象存储凭据。
                details={
                    "asset_id": str(asset.asset_id),
                    "expires_at": signed.expires_at.isoformat(),
                    "ttl_seconds": self._url_ttl_seconds,
                },
                occurred_at=now,
            ),
        )
        self._uow.commit()
        return signed
