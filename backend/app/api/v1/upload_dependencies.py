"""上传/RawAsset API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.ingestion import (
    ObjectStorage,
    RawAssetUseCases,
    UploadPolicy,
    UploadSessionUseCases,
)
from app.application.retention.use_cases import ReferenceImpactUseCases
from app.core.config import Settings, get_settings
from app.domain.ingestion.validation import DEFAULT_ALLOWED_CONTENT_TYPES
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.ingestion_directory import SqlCollectionDirectory
from app.infrastructure.repositories.raw_assets import (
    SqlRawAssetLinkRepository,
    SqlRawAssetRepository,
)
from app.infrastructure.repositories.retention import SqlReferenceImpactRepository
from app.infrastructure.repositories.upload_sessions import (
    SqlUploadPartChecksumBindingRepository,
    SqlUploadPartRepository,
    SqlUploadSessionRepository,
)
from app.infrastructure.storage.s3 import S3MultipartStorage

SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True, slots=True)
class UploadUseCases:
    uploads: UploadSessionUseCases
    assets: RawAssetUseCases
    #: V51-017 只读引用影响预览。与 assets 共享同一个 session/事务边界，
    #: 但它是独立用例对象：预览不写状态，不该走资产动作的幂等/审计路径。
    references: ReferenceImpactUseCases


@lru_cache(maxsize=2)
def _cached_object_storage() -> S3MultipartStorage:
    """复用 boto3 client；按进程缓存，配置变更需重启。"""
    return S3MultipartStorage(get_settings())


def get_object_storage() -> ObjectStorage:
    return _cached_object_storage()


ObjectStorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]


def get_upload_use_cases(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
) -> UploadUseCases:
    allowed = frozenset(
        item.strip().lower()
        for item in (settings.upload_allowed_content_types or DEFAULT_ALLOWED_CONTENT_TYPES)
    )
    policy = UploadPolicy(
        max_size_bytes=settings.upload_max_size_bytes,
        part_size_bytes=settings.upload_part_size_bytes,
        allowed_content_types=allowed,
        session_ttl=timedelta(hours=settings.upload_session_ttl_hours),
        url_ttl=timedelta(seconds=settings.upload_url_ttl_seconds),
    )
    sessions = SqlUploadSessionRepository(session)
    parts = SqlUploadPartRepository(session)
    bindings = SqlUploadPartChecksumBindingRepository(session)
    assets = SqlRawAssetRepository(session)
    links = SqlRawAssetLinkRepository(session)
    directory = SqlCollectionDirectory(session)
    return UploadUseCases(
        uploads=UploadSessionUseCases(
            sessions=sessions,
            parts=parts,
            bindings=bindings,
            assets=assets,
            links=links,
            directory=directory,
            storage=storage,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            idempotency=SqlIdempotencyRepository(session),
            uow=session,
            policy=policy,
        ),
        assets=RawAssetUseCases(
            assets,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            idempotency=SqlIdempotencyRepository(session),
            uow=session,
            # V51-017：销毁内容前的事务级引用阻断（蓝图 §20.4 要求不能只靠
            # 前端按钮）。装配在同一 session/事务里，锁与写共享事务边界。
            retention=ReferenceImpactUseCases(SqlReferenceImpactRepository(session)),
        ),
        references=ReferenceImpactUseCases(SqlReferenceImpactRepository(session)),
    )


UploadUseCasesDep = Annotated[UploadUseCases, Depends(get_upload_use_cases)]
