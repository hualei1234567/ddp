"""上传完成事务：ListParts 核对、内容签名、去重登记与审计。

完成流程可能在对象存储与数据库之间跨事务；可重试错误回到 UPLOADING，
安全/声明冲突进入 FAILED。整文件 Hash 与解析仍由 Worker 异步完成。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.ports import (
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
)
from app.application.ingestion.ports import (
    CompletePartSpec,
    CompleteUploadCommand,
    CompleteUploadResult,
    ObjectStorage,
    RawAssetLinkRepository,
    RawAssetRepository,
    StoredPart,
    UnitOfWork,
    UploadPartRepository,
    UploadSessionRepository,
)
from app.application.ingestion.upload_audit import (
    make_outbox_event,
    record_upload_audit,
    utcnow,
    uuid_ref,
)
from app.application.ingestion.upload_support import (
    complete_body,
    declare_format,
    validate_complete_parts,
)
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetLinkStatus,
    RawAssetLinkType,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
    UploadSessionAction,
    UploadSessionStatus,
)
from app.domain.ingestion.errors import (
    ConflictError,
    DomainValidationError,
    FileSignatureMismatchError,
    HashMismatchError,
    MultipartUploadNotFoundError,
    ObjectStorageUnavailableError,
    ResourceNotFoundError,
    SizeMismatchError,
    UploadIncompleteError,
    UploadPartConflictError,
    UploadSessionExpiredError,
    UploadSessionNotUploadingError,
    VersionConflictError,
)
from app.domain.ingestion.models import RawAsset, RawAssetLink, UploadPart, UploadSession
from app.domain.ingestion.validation import (
    HDF5_DETECTION_BYTES,
    canonical_content_type,
    detect_format,
    normalize_etag,
    normalize_sha256,
    required_part_count,
    signature_compatible,
)


class UploadCompletionService:
    def __init__(
        self,
        sessions: UploadSessionRepository,
        parts: UploadPartRepository,
        assets: RawAssetRepository,
        links: RawAssetLinkRepository,
        storage: ObjectStorage,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._sessions = sessions
        self._parts = parts
        self._assets = assets
        self._links = links
        self._storage = storage
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def complete(
        self,
        scope: ProjectScope,
        session_id: UUID,
        command: CompleteUploadCommand,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CompleteUploadResult:
        key = validate_key(idempotency_key)
        now = utcnow()
        body = complete_body(command)
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._completed_result(
                scope,
                self._load(scope, uuid_ref(replay.response_ref)),
                replayed=True,
            )

        session = self._active_session(scope, session_id, request_id=request_id)
        if session.status is UploadSessionStatus.COMPLETED:
            return self._completed_result(scope, session, replayed=True)
        if session.status not in {
            UploadSessionStatus.CREATED,
            UploadSessionStatus.UPLOADING,
            UploadSessionStatus.COMPLETING,
        }:
            raise UploadSessionNotUploadingError("会话不在可完成状态")
        if session.version != expected_version:
            raise VersionConflictError("上传会话")

        request_sha = normalize_sha256(command.expected_sha256)
        assert request_sha is not None
        if command.expected_size_bytes <= 0:
            raise DomainValidationError("expected_size_bytes 必须是正整数")
        request_parts = validate_complete_parts(command.parts)
        if session.expected_sha256 is not None and session.expected_sha256 != request_sha:
            self._fail_complete(
                scope,
                session,
                code=HashMismatchError.code,
                summary="整文件 Hash 与创建会话时的声明不一致",
                request_id=request_id,
                abort=True,
            )
            raise HashMismatchError("整文件 Hash 与创建会话时的声明不一致")

        local_parts = self._parts.list(scope, session_id)
        try:
            self._assert_local_matches(local_parts, request_parts)
        except UploadPartConflictError as exc:
            self._retryable_failure(
                scope,
                session,
                code=UploadPartConflictError.code,
                summary=str(exc),
                request_id=request_id,
            )
            raise
        storage_parts = self._list_storage_parts_or_recover(
            scope,
            session,
            expected_version=expected_version,
        )
        if isinstance(storage_parts, CompleteUploadResult):
            return storage_parts
        try:
            missing, storage_total = self._assert_storage_matches(
                session,
                request_parts,
                storage_parts,
                local_parts,
            )
        except UploadPartConflictError as exc:
            self._retryable_failure(
                scope,
                session,
                code=UploadPartConflictError.code,
                summary=str(exc),
                request_id=request_id,
            )
            raise
        if missing:
            self._retryable_failure(
                scope,
                session,
                code=UploadIncompleteError.code,
                summary="对象存储缺失分片",
                request_id=request_id,
            )
            raise UploadIncompleteError(missing)
        total = command.expected_size_bytes
        if (
            total != session.declared_size_bytes
            or storage_total != total
            or sum(part.size_bytes for part in request_parts) != total
        ):
            self._retryable_failure(
                scope,
                session,
                code=SizeMismatchError.code,
                summary="分片总大小与声明大小不一致",
                request_id=request_id,
            )
            raise SizeMismatchError(session.declared_size_bytes, total)

        completing = session
        if session.status is not UploadSessionStatus.COMPLETING:
            completing = self._sessions.save(
                scope,
                session.begin_complete(utcnow()),
                expected_version=session.version,
            )
        self._storage.complete_multipart(
            completing.object_key,
            completing.multipart_upload_id,
            storage_parts,
        )
        detected = self._detect_content(
            completing.object_key,
            completing.declared_size_bytes,
        )
        if not signature_compatible(declare_format(completing), detected):
            cleanup_pending = not self._safe_delete(completing.object_key)
            failed = completing.mark_failed(
                failure_code=FileSignatureMismatchError.code,
                failure_summary="对象内容签名与声明格式不一致",
                now=utcnow(),
            )
            saved = self._sessions.save(
                scope,
                failed,
                expected_version=completing.version,
            )
            record_upload_audit(
                self._audit,
                self._outbox,
                scope,
                session_id=completing.upload_session_id,
                action="complete",
                from_status=completing.status.value,
                to_status=saved.status.value,
                result="FAILURE",
                details={
                    "error_code": FileSignatureMismatchError.code,
                    "cleanup_pending": cleanup_pending,
                },
                request_id=request_id,
                event_type="ingestion.upload_session.failed",
                now=utcnow(),
            )
            self._uow.commit()
            raise FileSignatureMismatchError("对象内容签名与声明格式不一致")

        raw_format = detected or declare_format(completing)
        asset, deduplicated = self._register_asset(
            scope,
            completing,
            request_sha,
            raw_format,
            now=utcnow(),
        )
        dedup_cleanup_pending = False
        if (
            deduplicated
            and completing.capture_session_id is not None
            and asset.capture_session_id is None
        ):
            # 内容去重命中历史资产（如首次导入未带会话）：补写本次会话归属，
            # 否则本批素材不会出现在该会话的产物列表里。已有归属时保留最早值。
            if self._assets.attach_capture_session_if_absent(
                scope,
                asset.asset_id,
                capture_session_id=completing.capture_session_id,
            ):
                refreshed = self._assets.get(scope, asset.asset_id)
                if refreshed is not None:
                    asset = refreshed
        if deduplicated and asset.object_key != completing.object_key:
            # 内容重复时新对象不再保留；删除失败交给后台清理重试。
            dedup_cleanup_pending = not self._safe_delete(completing.object_key)
            if dedup_cleanup_pending:
                self._outbox.enqueue(
                    scope,
                    make_outbox_event(
                        scope,
                        aggregate_id=completing.upload_session_id,
                        event_type="ingestion.upload_session.cleanup_requested",
                        payload={
                            "upload_session_id": str(completing.upload_session_id),
                            "object_key": completing.object_key,
                            "multipart_upload_id": completing.multipart_upload_id,
                        },
                        now=utcnow(),
                    ),
                )
        self._ensure_link(scope, completing, asset, now=utcnow())
        job_id = completing.validation_job_id or uuid4()
        saved = self._sessions.save(
            scope,
            completing.mark_completed(
                asset_id=asset.asset_id,
                deduplicated=deduplicated,
                deduplicated_asset_id=asset.asset_id if deduplicated else None,
                validation_job_id=job_id,
                now=utcnow(),
            ),
            expected_version=completing.version,
        )
        record_upload_audit(
            self._audit,
            self._outbox,
            scope,
            session_id=completing.upload_session_id,
            action="complete",
            from_status=completing.status.value,
            to_status=saved.status.value,
            result="SUCCESS",
            details={
                "asset_id": str(asset.asset_id),
                "deduplicated": deduplicated,
                "dedup_cleanup_pending": dedup_cleanup_pending,
                "part_count": len(request_parts),
                "validation_job_id": str(job_id),
            },
            request_id=request_id,
            event_type="ingestion.upload_session.completed",
            now=utcnow(),
        )
        self._outbox.enqueue(
            scope,
            make_outbox_event(
                scope,
                aggregate_id=asset.asset_id,
                event_type="ingestion.raw_asset.validation_requested",
                payload={
                    "asset_id": str(asset.asset_id),
                    "validation_job_id": str(job_id),
                    "upload_session_id": str(saved.upload_session_id),
                    "actor_user_id": str(scope.actor_id),
                    # Worker 据此恢复发起请求的 trace_id；缺失时由 Worker
                    # 生成并标注 generated，不拿 event_id 冒充。
                    **({"request_id": request_id} if request_id is not None else {}),
                },
                now=utcnow(),
                aggregate_type="RAW_ASSET",
                # 投递类：worker 要据此真的去校验资产（R-13 / T-1）。
                dispatch=True,
            ),
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=202,
            response_ref=str(saved.upload_session_id),
            now=utcnow(),
        )
        self._uow.commit()
        return CompleteUploadResult(
            session=saved,
            raw_asset=asset,
            deduplicated=deduplicated,
            validation_job_id=job_id,
            replayed=False,
            parts=tuple(local_parts),
        )

    # ------------------------------------------------------------------ 内部
    def _list_storage_parts_or_recover(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        expected_version: int,
    ) -> list[StoredPart] | CompleteUploadResult:
        """读取 S3 分片；multipart 已消失时按会话最终状态收敛。

        并发 complete 中，输家可能在赢家提交后才调用 ListParts，收到
        ``NoSuchUpload``。此时必须重读会话，而不是把存储信号映射为 503：
        - 会话已 COMPLETED：返回首次结果（HTTP 202 + 幂等重放）；
        - 会话版本已推进（例如 COMPLETING）：409 version_conflict；
        - 其余情况：409 upload_incomplete。
        """
        try:
            return self._storage.list_parts(
                session.object_key,
                session.multipart_upload_id,
            )
        except MultipartUploadNotFoundError:
            current = self._load(scope, session.upload_session_id)
            if current.status is UploadSessionStatus.COMPLETED:
                return self._completed_result(scope, current, replayed=True)
            if current.version != expected_version:
                raise VersionConflictError("上传会话") from None
            raise UploadIncompleteError([]) from None

    def _load(self, scope: ProjectScope, session_id: UUID) -> UploadSession:
        session = self._sessions.get(scope, session_id)
        if session is None:
            raise ResourceNotFoundError("上传会话", session_id)
        return session

    def _active_session(
        self,
        scope: ProjectScope,
        session_id: UUID,
        *,
        request_id: str | None,
    ) -> UploadSession:
        session = self._load(scope, session_id)
        now = utcnow()
        if (
            session.status
            in {
                UploadSessionStatus.CREATED,
                UploadSessionStatus.UPLOADING,
            }
            and now >= session.expires_at
        ):
            saved = self._sessions.save(
                scope,
                session.expire(now),
                expected_version=session.version,
            )
            record_upload_audit(
                self._audit,
                self._outbox,
                scope,
                session_id=session.upload_session_id,
                action=UploadSessionAction.SYSTEM_EXPIRE.value,
                from_status=session.status.value,
                to_status=saved.status.value,
                result="SUCCESS",
                details={"expires_at": session.expires_at.isoformat()},
                request_id=request_id,
                event_type="ingestion.upload_session.expired",
                now=now,
            )
            self._uow.commit()
            raise UploadSessionExpiredError("上传会话已过期，请新建会话")
        if session.status is UploadSessionStatus.EXPIRED:
            raise UploadSessionExpiredError("上传会话已过期，请新建会话")
        return session

    def _assert_local_matches(
        self,
        local_parts: list[UploadPart],
        request_parts: tuple[CompletePartSpec, ...],
    ) -> None:
        local_by_number = {part.part_number: part for part in local_parts}
        if len(local_by_number) != len(request_parts):
            raise UploadPartConflictError("请求分片清单与本地确认记录不一致")
        for spec in request_parts:
            local = local_by_number.get(spec.part_number)
            if local is None:
                raise UploadPartConflictError("请求分片清单与本地确认记录不一致")
            if (
                local.size_bytes != spec.size_bytes
                or local.etag != normalize_etag(spec.etag)
                or local.sha256 != normalize_sha256(spec.sha256)
            ):
                raise UploadPartConflictError("请求分片清单与本地确认记录不一致")

    def _assert_storage_matches(
        self,
        session: UploadSession,
        request_parts: tuple[CompletePartSpec, ...],
        storage_parts: list[StoredPart],
        local_parts: list[UploadPart],
    ) -> tuple[list[int], int]:
        expected_numbers = set(
            range(
                1,
                required_part_count(
                    session.declared_size_bytes,
                    session.part_size_bytes,
                )
                + 1,
            )
        )
        request_numbers = {spec.part_number for spec in request_parts}
        if not request_numbers <= expected_numbers:
            raise UploadPartConflictError("请求包含超出文件范围的分片号")
        storage_by_number = {part.part_number: part for part in storage_parts}
        missing = sorted(expected_numbers - set(storage_by_number))
        if missing:
            return missing, sum(part.size_bytes for part in storage_parts)
        local_by_number = {part.part_number: part for part in local_parts}
        for spec in request_parts:
            storage = storage_by_number[spec.part_number]
            if storage.size_bytes != spec.size_bytes or normalize_etag(
                storage.etag
            ) != normalize_etag(spec.etag):
                raise UploadPartConflictError("对象存储分片与确认记录不一致")
            local = local_by_number[spec.part_number]
            if storage.checksum_sha256 is not None and storage.checksum_sha256 != local.sha256:
                raise UploadPartConflictError("对象存储分片校验和与确认记录不一致")
        if set(storage_by_number) != expected_numbers:
            raise UploadPartConflictError("对象存储存在未声明的分片")
        return [], sum(part.size_bytes for part in storage_parts)

    def _retryable_failure(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        code: str,
        summary: str,
        request_id: str | None,
    ) -> None:
        now = utcnow()
        if session.status is UploadSessionStatus.CREATED:
            updated = session.begin_upload(now)
            updated = _with_failure(updated, code, summary, now)
        elif session.status is UploadSessionStatus.COMPLETING:
            updated = session.retry_upload(
                failure_code=code,
                failure_summary=summary,
                now=now,
            )
        else:
            updated = _with_failure(session, code, summary, now)
        saved = self._sessions.save(scope, updated, expected_version=session.version)
        record_upload_audit(
            self._audit,
            self._outbox,
            scope,
            session_id=session.upload_session_id,
            action="complete",
            from_status=session.status.value,
            to_status=saved.status.value,
            result="FAILURE",
            details={"error_code": code},
            request_id=request_id,
            event_type="ingestion.upload_session.retryable_failure",
            now=now,
        )
        self._uow.commit()

    def _fail_complete(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        code: str,
        summary: str,
        request_id: str | None,
        abort: bool,
    ) -> None:
        now = utcnow()
        cleanup_pending = False
        if abort:
            try:
                self._storage.abort_multipart(
                    session.object_key,
                    session.multipart_upload_id,
                )
            except ObjectStorageUnavailableError:
                cleanup_pending = True
        failed = session.mark_failed(
            failure_code=code,
            failure_summary=summary,
            now=now,
        )
        saved = self._sessions.save(scope, failed, expected_version=session.version)
        record_upload_audit(
            self._audit,
            self._outbox,
            scope,
            session_id=session.upload_session_id,
            action="complete",
            from_status=session.status.value,
            to_status=saved.status.value,
            result="FAILURE",
            details={
                "error_code": code,
                "cleanup_pending": cleanup_pending,
            },
            request_id=request_id,
            event_type="ingestion.upload_session.failed",
            now=now,
        )
        self._uow.commit()

    def _detect_content(
        self,
        object_key: str,
        size_bytes: int,
    ) -> RawAssetFormat | None:
        prefix = self._storage.read_range(
            object_key,
            start=0,
            length=min(HDF5_DETECTION_BYTES, size_bytes),
        )
        suffix_start = max(0, size_bytes - 4)
        suffix = self._storage.read_range(object_key, start=suffix_start, length=4)
        return detect_format(prefix, suffix)

    def _register_asset(
        self,
        scope: ProjectScope,
        session: UploadSession,
        sha256: str,
        raw_format: RawAssetFormat,
        *,
        now: datetime,
    ) -> tuple[RawAsset, bool]:
        existing = self._assets.find_active_by_content(
            scope,
            sha256=sha256,
            size_bytes=session.declared_size_bytes,
        )
        if existing is not None:
            return existing, True
        asset_id = session.asset_id or uuid4()
        asset = RawAsset(
            asset_id=asset_id,
            project_id=scope.project_id,
            code=f"RAW-{asset_id.hex[:20]}",
            source_type=RawAssetSourceType.UPLOAD,
            collection_task_id=session.collection_task_id,
            batch_id=session.batch_id,
            upload_session_id=session.upload_session_id,
            capture_session_id=session.capture_session_id,
            original_filename=session.original_filename,
            object_key=session.object_key,
            size_bytes=session.declared_size_bytes,
            content_type=canonical_content_type(raw_format),
            format=raw_format,
            sha256=sha256,
            status=RawAssetStatus.VALIDATING,
            ingested_at=now,
            storage_class=StorageClass.STANDARD,
            uploaded_by=scope.actor_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        try:
            created = self._assets.add(scope, asset)
        except ConflictError:
            winner = self._assets.find_active_by_content(
                scope,
                sha256=sha256,
                size_bytes=session.declared_size_bytes,
            )
            if winner is None:
                raise
            return winner, True
        return created, False

    def _ensure_link(
        self,
        scope: ProjectScope,
        session: UploadSession,
        asset: RawAsset,
        *,
        now: datetime,
    ) -> None:
        if self._links.exists_for_session(
            scope,
            asset_id=asset.asset_id,
            upload_session_id=session.upload_session_id,
            link_type=RawAssetLinkType.UPLOAD_SESSION.value,
        ):
            return
        try:
            self._links.add(
                scope,
                RawAssetLink(
                    link_id=uuid4(),
                    project_id=scope.project_id,
                    asset_id=asset.asset_id,
                    collection_task_id=session.collection_task_id,
                    batch_id=session.batch_id,
                    upload_session_id=session.upload_session_id,
                    link_type=RawAssetLinkType.UPLOAD_SESSION,
                    link_status=RawAssetLinkStatus.PENDING_VERIFICATION,
                    created_by=scope.actor_id,
                    created_at=now,
                ),
            )
        except ConflictError:
            # 并发完成时另一请求已建立同一使用关系；不重复插入。
            if self._links.exists_for_session(
                scope,
                asset_id=asset.asset_id,
                upload_session_id=session.upload_session_id,
                link_type=RawAssetLinkType.UPLOAD_SESSION.value,
            ):
                return
            raise

    def _completed_result(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        replayed: bool,
    ) -> CompleteUploadResult:
        if session.status is not UploadSessionStatus.COMPLETED:
            raise ConflictError("幂等记录与上传会话状态不一致")
        asset = self._assets.get(scope, session.asset_id) if session.asset_id is not None else None
        if session.validation_job_id is None:
            raise ConflictError("已完成会话缺少校验任务 ID")
        return CompleteUploadResult(
            session=session,
            raw_asset=asset,
            deduplicated=session.deduplicated,
            validation_job_id=session.validation_job_id,
            replayed=replayed,
            parts=tuple(self._parts.list(scope, session.upload_session_id)),
        )

    def _safe_delete(self, object_key: str) -> bool:
        try:
            self._storage.delete_object(object_key)
        except ObjectStorageUnavailableError:
            return False
        return True


def _with_failure(
    session: UploadSession,
    code: str,
    summary: str,
    now: datetime,
) -> UploadSession:
    from dataclasses import replace

    return replace(
        session,
        failure_code=code,
        failure_summary=summary,
        updated_at=now,
    )
