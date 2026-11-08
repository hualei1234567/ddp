"""上传会话生命周期：创建、断点查询、分片 URL/确认与中止。

完成事务拆到 :mod:`app.application.ingestion.upload_completion`；本模块只做
传输态元数据编排。HTTP 线程不读取大文件。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from app.application.collection.audit import record_change
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.ports import (
    AuditRepository,
    IdempotencyRepository,
    OutboxRepository,
)
from app.application.ingestion.ports import (
    AbortUploadSessionCommand,
    AckUploadPartCommand,
    AckUploadPartResult,
    CollectionDirectory,
    CompleteUploadCommand,
    CompleteUploadResult,
    CreatedUploadSession,
    CreateUploadSessionCommand,
    ObjectStorage,
    PresignedPart,
    RawAssetLinkRepository,
    RawAssetRepository,
    UnitOfWork,
    UploadPartRepository,
    UploadPolicy,
    UploadSessionDetail,
    UploadSessionRepository,
)
from app.application.ingestion.upload_audit import (
    make_outbox_event,
    record_upload_audit,
    utcnow,
    uuid_ref,
)
from app.application.ingestion.upload_completion import UploadCompletionService
from app.application.ingestion.upload_support import (
    MAX_PART_NUMBER,
    command_body,
)
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    UploadSessionAction,
    UploadSessionStatus,
)
from app.domain.ingestion.errors import (
    DomainValidationError,
    FileTooLargeError,
    ObjectStorageUnavailableError,
    ResourceNotFoundError,
    SizeMismatchError,
    UploadPartConflictError,
    UploadSessionExpiredError,
    UploadSessionNotUploadingError,
    VersionConflictError,
)
from app.domain.ingestion.models import UploadPart, UploadSession
from app.domain.ingestion.validation import (
    build_raw_object_key,
    expected_part_size,
    normalize_content_type,
    normalize_etag,
    normalize_sha256,
    required_part_count,
    resolve_declared_format,
    sanitize_filename,
    validate_part_number,
)

_RESOURCE_TYPE = "UPLOAD_SESSION"


class UploadSessionUseCases:
    def __init__(
        self,
        sessions: UploadSessionRepository,
        parts: UploadPartRepository,
        assets: RawAssetRepository,
        links: RawAssetLinkRepository,
        directory: CollectionDirectory,
        storage: ObjectStorage,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        policy: UploadPolicy,
    ) -> None:
        self._sessions = sessions
        self._parts = parts
        self._directory = directory
        self._storage = storage
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._policy = policy
        self._completion = UploadCompletionService(
            sessions=sessions,
            parts=parts,
            assets=assets,
            links=links,
            storage=storage,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=uow,
        )

    # ------------------------------------------------------------------ 创建
    def create_session(
        self,
        scope: ProjectScope,
        command: CreateUploadSessionCommand,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> CreatedUploadSession:
        key = validate_key(idempotency_key)
        now = utcnow()
        body = command_body(command)
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return CreatedUploadSession(
                session=self._load(scope, uuid_ref(replay.response_ref)),
                replayed=True,
            )

        filename = sanitize_filename(command.original_filename)
        content_type = normalize_content_type(
            command.declared_content_type,
            self._policy.allowed_content_types,
        )
        declared_format = resolve_declared_format(content_type, command.format_hint)
        declared_size = command.declared_size_bytes
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size <= 0
        ):
            raise DomainValidationError("declared_size_bytes 必须是正整数")
        if declared_size > self._policy.max_size_bytes:
            raise FileTooLargeError("文件超过会话允许的最大大小")
        if required_part_count(declared_size, self._policy.part_size_bytes) > MAX_PART_NUMBER:
            raise DomainValidationError("文件分片数量超过上限")
        expected_sha = normalize_sha256(command.expected_sha256, required=False)
        self._validate_collection_refs(
            scope,
            command.collection_task_id,
            command.batch_id,
        )

        asset_id = uuid4()
        object_key = build_raw_object_key(
            scope.project_id,
            asset_id,
            now,
            declared_format,
        )
        upload_id = self._storage.initiate_multipart(object_key)
        session = UploadSession(
            upload_session_id=uuid4(),
            project_id=scope.project_id,
            collection_task_id=command.collection_task_id,
            batch_id=command.batch_id,
            original_filename=filename,
            declared_content_type=content_type,
            declared_size_bytes=declared_size,
            expected_sha256=expected_sha,
            format_hint=declared_format,
            part_size_bytes=self._policy.part_size_bytes,
            max_size_bytes=self._policy.max_size_bytes,
            allowed_content_types=tuple(sorted(self._policy.allowed_content_types)),
            object_key=object_key,
            multipart_upload_id=upload_id,
            expires_at=now + self._policy.session_ttl,
            asset_id=asset_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._sessions.add(scope, session)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=created.upload_session_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "declared_size_bytes": created.declared_size_bytes,
                "declared_content_type": created.declared_content_type,
                "part_size_bytes": created.part_size_bytes,
            },
            event_type="ingestion.upload_session.created",
            event_payload={
                "upload_session_id": str(created.upload_session_id),
                "status": created.status.value,
                "object_key": created.object_key,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.upload_session_id),
            now=now,
        )
        self._uow.commit()
        return CreatedUploadSession(session=created, replayed=False)

    # -------------------------------------------------------------- 断点查询
    def get_session(
        self,
        scope: ProjectScope,
        session_id: UUID,
        *,
        request_id: str | None = None,
    ) -> UploadSessionDetail:
        session = self._active_session(scope, session_id, request_id=request_id)
        local_parts = self._parts.list(scope, session_id)
        if session.is_terminal:
            return UploadSessionDetail(
                session=session,
                parts=local_parts,
                uploaded_bytes=session.uploaded_bytes,
                uploaded_part_count=session.uploaded_part_count,
                missing_part_numbers=[],
            )
        storage_parts = self._storage.list_parts(
            session.object_key,
            session.multipart_upload_id,
        )
        expected = set(
            range(
                1,
                required_part_count(
                    session.declared_size_bytes,
                    session.part_size_bytes,
                )
                + 1,
            )
        )
        present = {part.part_number for part in storage_parts}
        return UploadSessionDetail(
            session=session,
            parts=local_parts,
            uploaded_bytes=sum(part.size_bytes for part in storage_parts),
            uploaded_part_count=len(storage_parts),
            missing_part_numbers=sorted(expected - present),
        )

    def list_parts(
        self,
        scope: ProjectScope,
        session_id: UUID,
        *,
        request_id: str | None = None,
    ) -> UploadSessionDetail:
        return self.get_session(scope, session_id, request_id=request_id)

    # ----------------------------------------------------------- 分片 URL
    def create_part_url(
        self,
        scope: ProjectScope,
        session_id: UUID,
        part_number: int,
        *,
        request_id: str | None = None,
    ) -> PresignedPart:
        session = self._active_session(scope, session_id, request_id=request_id)
        if session.status not in {
            UploadSessionStatus.CREATED,
            UploadSessionStatus.UPLOADING,
        }:
            raise UploadSessionNotUploadingError("会话不在可上传状态")
        validate_part_number(part_number)
        size_bytes = expected_part_size(
            part_number,
            session.declared_size_bytes,
            session.part_size_bytes,
        )
        if session.status is UploadSessionStatus.CREATED:
            now = utcnow()
            updated = self._sessions.save(
                scope,
                session.begin_upload(now),
                expected_version=session.version,
            )
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE_TYPE,
                resource_id=updated.upload_session_id,
                action=UploadSessionAction.BEGIN_UPLOAD.value,
                from_status=session.status.value,
                to_status=updated.status.value,
                reason=None,
                request_id=request_id,
                details={"part_number": part_number},
                event_type="ingestion.upload_session.status_changed",
                event_payload={
                    "upload_session_id": str(updated.upload_session_id),
                    "from_status": session.status.value,
                    "to_status": updated.status.value,
                },
                occurred_at=now,
            )
            self._uow.commit()
        return self._storage.presign_upload_part(
            session.object_key,
            session.multipart_upload_id,
            part_number=part_number,
            content_length=size_bytes,
            checksum_sha256_base64=None,
            expires_in_seconds=max(1, int(self._policy.url_ttl.total_seconds())),
        )

    # --------------------------------------------------------------- 分片确认
    def ack_part(
        self,
        scope: ProjectScope,
        session_id: UUID,
        part_number: int,
        command: AckUploadPartCommand,
        *,
        request_id: str | None = None,
    ) -> AckUploadPartResult:
        session = self._active_session(scope, session_id, request_id=request_id)
        if session.status not in {
            UploadSessionStatus.CREATED,
            UploadSessionStatus.UPLOADING,
        }:
            raise UploadSessionNotUploadingError("会话不在可上传状态")
        validate_part_number(part_number)
        expected_size = expected_part_size(
            part_number,
            session.declared_size_bytes,
            session.part_size_bytes,
        )
        if command.size_bytes != expected_size:
            raise SizeMismatchError(expected_size, command.size_bytes)
        etag = normalize_etag(command.etag)
        sha256 = normalize_sha256(command.sha256)
        assert sha256 is not None
        now = utcnow()

        existing = self._parts.get(scope, session_id, part_number)
        if existing is not None:
            if (
                existing.size_bytes == command.size_bytes
                and existing.etag == etag
                and existing.sha256 == sha256
            ):
                parts = self._parts.list(scope, session_id)
                return _ack_result(existing, parts)
            raise UploadPartConflictError("同分片号的内容与既有记录不一致")

        part = UploadPart(
            project_id=scope.project_id,
            upload_session_id=session_id,
            part_number=part_number,
            size_bytes=command.size_bytes,
            etag=etag,
            sha256=sha256,
            recorded_at=now,
        )
        self._parts.add(scope, part)
        parts = self._parts.list(scope, session_id)
        status_changed = session.status is UploadSessionStatus.CREATED
        updated = self._sessions.set_progress(
            scope,
            session_id,
            uploaded_bytes=sum(item.size_bytes for item in parts),
            uploaded_part_count=len(parts),
            status=(UploadSessionStatus.UPLOADING.value if status_changed else None),
            now=now,
        )
        if status_changed:
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_RESOURCE_TYPE,
                resource_id=session_id,
                action=UploadSessionAction.BEGIN_UPLOAD.value,
                from_status=session.status.value,
                to_status=updated.status.value,
                reason=None,
                request_id=request_id,
                details={"part_number": part_number},
                event_type="ingestion.upload_session.status_changed",
                event_payload={
                    "upload_session_id": str(session_id),
                    "from_status": session.status.value,
                    "to_status": updated.status.value,
                },
                occurred_at=now,
            )
        self._uow.commit()
        return _ack_result(part, parts)

    # --------------------------------------------------------------- 完成
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
        return self._completion.complete(
            scope,
            session_id,
            command,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )

    # --------------------------------------------------------------- 中止
    def abort(
        self,
        scope: ProjectScope,
        session_id: UUID,
        command: AbortUploadSessionCommand,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> UploadSession:
        key = validate_key(idempotency_key)
        now = utcnow()
        body = {"reason": command.reason}
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self._load(scope, uuid_ref(replay.response_ref))

        session = self._load(scope, session_id)
        if session.status in {
            UploadSessionStatus.ABORTED,
            UploadSessionStatus.COMPLETED,
            UploadSessionStatus.EXPIRED,
            UploadSessionStatus.FAILED,
        }:
            return session
        if session.status not in {
            UploadSessionStatus.CREATED,
            UploadSessionStatus.UPLOADING,
        }:
            raise UploadSessionNotUploadingError("会话当前状态不可中止")
        if session.version != expected_version:
            raise VersionConflictError("上传会话")

        cleanup_error: str | None = None
        try:
            self._storage.abort_multipart(
                session.object_key,
                session.multipart_upload_id,
            )
        except ObjectStorageUnavailableError:
            cleanup_error = ObjectStorageUnavailableError.code

        updated = session.abort(reason=command.reason, now=now)
        if cleanup_error is not None:
            updated = replace(
                updated,
                failure_code=cleanup_error,
                failure_summary="终止 multipart 失败，等待后台清理重试",
            )
        saved = self._sessions.save(scope, updated, expected_version=session.version)
        record_upload_audit(
            self._audit,
            self._outbox,
            scope,
            session_id=saved.upload_session_id,
            action=UploadSessionAction.ABORT.value,
            from_status=session.status.value,
            to_status=saved.status.value,
            result="SUCCESS",
            details={"cleanup_pending": cleanup_error is not None},
            request_id=request_id,
            event_type="ingestion.upload_session.aborted",
            now=now,
        )
        if cleanup_error is not None:
            self._outbox.enqueue(
                scope,
                make_outbox_event(
                    scope,
                    aggregate_id=saved.upload_session_id,
                    event_type="ingestion.upload_session.cleanup_requested",
                    payload={
                        "upload_session_id": str(saved.upload_session_id),
                        "object_key": saved.object_key,
                        "multipart_upload_id": saved.multipart_upload_id,
                    },
                    now=now,
                ),
            )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.upload_session_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # --------------------------------------------------------------- 内部
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

    def _validate_collection_refs(
        self,
        scope: ProjectScope,
        task_id: UUID | None,
        batch_id: UUID | None,
    ) -> None:
        if batch_id is not None and task_id is None:
            raise DomainValidationError("batch_id 必须与 collection_task_id 同时提供")
        if task_id is not None and not self._directory.task_exists(scope, task_id):
            raise ResourceNotFoundError("采集任务", task_id)
        if batch_id is not None:
            assert task_id is not None
            if not self._directory.batch_belongs(
                scope,
                batch_id=batch_id,
                task_id=task_id,
            ):
                raise ResourceNotFoundError("采集批次", batch_id)


def _ack_result(part: UploadPart, parts: list[UploadPart]) -> AckUploadPartResult:
    return AckUploadPartResult(
        part=part,
        uploaded_bytes=sum(item.size_bytes for item in parts),
        uploaded_part_count=len(parts),
    )
