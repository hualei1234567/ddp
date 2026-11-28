"""数据源用例：登记、改档、导入任务编排。

不改写既有模块的规则：幂等、审计、乐观锁都复用
``app.application.{idempotency,audit}`` 的权威实现。
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.datasources.commands import (
    DataSourcePatch,
    NewDataSource,
    NewDataSourceImport,
)
from app.application.datasources.events import (
    DATASOURCE_IMPORT_ACTION_EVENTS,
    DATASOURCE_IMPORT_SUCCEEDED_EVENT,
)
from app.application.datasources.idempotency import IdempotencyGuard, validate_key
from app.application.datasources.ports import (
    AuditRepository,
    DataSourceImportLogRepository,
    DataSourceImportRepository,
    DataSourceRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasources.verification import (
    REMOTE_CHECK_METADATA_KEY,
    DataSourceDetail,
    RemoteCheckError,
    RemoteCheckResult,
    RemoteSourceVerifier,
    build_remote_check_failure_details,
    build_remote_check_metadata,
    read_remote_check,
)
from app.application.observability_redaction import sanitize_log_message
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasources.license_gate import enforce_license_gate
from app.domain.datasources.models import DataSource, DataSourceImport, DataSourceImportLog
from app.domain.datasources.state_machines import (
    allowed_import_actions,
    import_target_status,
    validate_import_action,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_SOURCE = "data_source"
_RESOURCE_IMPORT = "data_source_import"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DataSourceUseCases:
    def __init__(
        self,
        sources: DataSourceRepository,
        imports: DataSourceImportRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        logs: DataSourceImportLogRepository,
        verifier: RemoteSourceVerifier | None = None,
    ) -> None:
        self._sources = sources
        self._imports = imports
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        self._logs = logs
        # 远端校验是**显式可选**能力：未配置时 verify_remote=true 必须失败，
        # 不能悄悄当作"校验通过"。
        self._verifier = verifier

    # ---------- 数据源 ----------

    def list_sources(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSource]:
        return self._sources.list(scope, kind=kind, after_id=after_id, limit=limit)

    def get_source(self, scope: ProjectScope, data_source_id: UUID) -> DataSource:
        item = self._sources.get(scope, data_source_id)
        if item is None:
            raise ResourceNotFoundError("数据源", data_source_id)
        return item

    def get_source_detail(
        self,
        scope: ProjectScope,
        data_source_id: UUID,
        *,
        import_limit: int = 20,
    ) -> DataSourceDetail:
        """详情聚合：登记信息、导入任务与服务端记录的校验结论。

        校验结论只从 ``metadata`` 的服务端保留键读取；缺失或形状不合法一律
        返回 ``None``（未校验），不把坏值渲染成"已校验"。
        """
        source = self.get_source(scope, data_source_id)
        imports = self._imports.list(
            scope,
            data_source_id=data_source_id,
            limit=import_limit,
        )
        return DataSourceDetail(
            source=source,
            imports=imports,
            remote_check=read_remote_check(source.metadata),
        )

    def create_source(
        self,
        scope: ProjectScope,
        command: NewDataSource,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataSource:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_source(scope, replay.response_ref)

        if self._sources.get_by_code(scope, command.code) is not None:
            raise DuplicateResourceError("项目内数据源编码重复")
        # 同一 repo + revision + config + split 只登记一次，否则血缘分叉。
        if (
            self._sources.get_by_revision(
                scope,
                repo_id=command.repo_id,
                revision=command.revision,
                config_name=command.config_name,
                split=command.split,
            )
            is not None
        ):
            raise DuplicateResourceError("同一 repo/revision/config/split 已登记")

        # 本地规则先判：重复登记不必访问外网。远端校验是显式可选步骤。
        metadata = dict(command.metadata or {})
        if REMOTE_CHECK_METADATA_KEY in metadata:
            raise DomainValidationError(
                f"metadata.{REMOTE_CHECK_METADATA_KEY} 为服务端保留字段，客户端不得写入"
            )
        if command.verify_remote:
            remote_check = self._verify_remote(command)
            metadata[REMOTE_CHECK_METADATA_KEY] = build_remote_check_metadata(
                remote_check,
                checked_at=now,
            )

        item = DataSource(
            data_source_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            kind=command.kind,
            repo_id=command.repo_id,
            revision=command.revision,
            license=command.license,
            access_condition=command.access_condition,
            is_gated=command.is_gated,
            config_name=command.config_name,
            split=command.split,
            dataset_card_url=command.dataset_card_url,
            notes=command.notes,
            metadata=metadata,
        )
        created = self._sources.add(scope, item)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_SOURCE,
            resource_id=created.data_source_id,
            action="register",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={
                "code": created.code,
                "repo_id": created.repo_id,
                "revision": created.revision,
                "license": created.license,
                "access_condition": str(created.access_condition),
                "is_gated": created.is_gated,
                "remote_verified": command.verify_remote,
            },
            event_type="datasource.registered",
            event_payload={
                "data_source_id": str(created.data_source_id),
                "repo_id": created.repo_id,
                "revision": created.revision,
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
            response_ref=str(created.data_source_id),
            now=now,
        )
        self._uow.commit()
        return created

    def _verify_remote(self, command: NewDataSource) -> RemoteCheckResult:
        """显式远端校验：失败一律拒绝登记，不降级为"未校验但继续"。"""
        if self._verifier is None:
            raise DomainValidationError("服务端未配置远端校验器，无法执行 verify_remote")
        try:
            result = self._verifier.describe(
                command.repo_id,
                revision=command.revision,
            )
        except RemoteCheckError as exc:
            # 失败必须显式记录：响应体带稳定的 ``details.remote_check``
            # （status=FAILED + 枚举原因），供前端区分超时/404/5xx/非法响应；
            # 远端原始报错只进服务端日志（已脱敏），绝不回显。
            raise DomainValidationError(
                f"远端校验失败，数据源未登记：{sanitize_log_message(exc)}",
                details=build_remote_check_failure_details(exc.reason),
            ) from exc

        if result.repo_id != command.repo_id.strip():
            raise DomainValidationError("远端返回的 repo_id 与登记值不一致")
        # 登记 revision 本身就是 commit sha；远端解析结果必须与它一致，
        # 否则说明远端忽略了 revision 或数据集已不可复现。
        if result.resolved_revision != command.revision:
            raise DomainValidationError("远端解析的 commit sha 与登记 revision 不一致")
        # gated 是合规事实，不能只看声明。两个方向的不一致都必须拒绝：
        # 声明 gated 实际公开 = 过度限制；声明公开实际 gated = 合规误判。
        if result.is_gated != command.is_gated:
            raise DomainValidationError("远端 gated 标记与 is_gated 声明不一致")
        if result.is_gated and command.access_condition == "PUBLIC":
            raise DomainValidationError("远端数据集为 gated，不能登记为 PUBLIC")
        return result

    def update_source(
        self,
        scope: ProjectScope,
        data_source_id: UUID,
        patch: DataSourcePatch,
        *,
        expected_version: int,
        request_id: str | None = None,
    ) -> DataSource:
        current = self.get_source(scope, data_source_id)
        if current.row_version != expected_version:
            raise VersionConflictError("数据源")
        updates: dict[str, object] = {}
        for name in (
            "name",
            "license",
            "access_condition",
            "is_gated",
            "config_name",
            "split",
            "notes",
        ):
            value = getattr(patch, name)
            if value is not None:
                updates[name] = value
        if not updates:
            raise DomainValidationError("没有需要修改的字段")
        # repo_id / revision 不可改：改 revision 等于换数据，必须重新登记。
        now = _utcnow()
        updated = replace(current, **updates, row_version=current.row_version + 1)
        saved = self._sources.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_SOURCE,
            resource_id=saved.data_source_id,
            action="update",
            from_status=None,
            to_status=None,
            reason=None,
            request_id=request_id,
            details={"changed_fields": sorted(updates)},
            event_type="datasource.updated",
            event_payload={
                "data_source_id": str(saved.data_source_id),
                "changed_fields": sorted(updates),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    # ---------- 导入任务 ----------

    def list_imports(
        self,
        scope: ProjectScope,
        *,
        data_source_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSourceImport]:
        return self._imports.list(
            scope,
            data_source_id=data_source_id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def get_import(self, scope: ProjectScope, import_id: UUID) -> DataSourceImport:
        item = self._imports.get(scope, import_id)
        if item is None:
            raise ResourceNotFoundError("导入任务", import_id)
        return item

    def list_import_logs(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        limit: int = 100,
        before_log_id: UUID | None = None,
    ) -> tuple[list[DataSourceImportLog], bool]:
        """读取一次导入的步骤日志（最近 ``limit`` 条，时间正序）。

        先 ``get_import``：跨项目或不存在一律 404，不能靠"空日志列表"泄露
        "该 ID 存在与否"的差异。

        ``before_log_id`` 为上一页最旧一条日志的 ID，用于按游标加载更早的
        历史；返回值第二项表示是否还有更早的日志（即 ``next_cursor`` 非空）。
        """
        self.get_import(scope, import_id)
        return self._logs.list_recent(
            scope,
            import_id,
            limit=limit,
            before_log_id=before_log_id,
        )

    def create_import(
        self,
        scope: ProjectScope,
        data_source_id: UUID,
        command: NewDataSourceImport,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataSourceImport:
        """登记一次导入；**不在此处下载数据**，只建任务并投递事件。"""
        key = validate_key(idempotency_key)
        body = {
            "data_source_id": str(data_source_id),
            "scope": command.scope,
            "tool_name": command.tool_name,
            "tool_version": command.tool_version,
            "sample_limit": command.sample_limit,
            "collection_task_id": (
                str(command.collection_task_id) if command.collection_task_id else None
            ),
            "batch_id": str(command.batch_id) if command.batch_id else None,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_import(scope, replay.response_ref)

        source = self.get_source(scope, data_source_id)
        # V61-023 许可门禁：在登记导入**之前**拦截，避免"先落一条 PENDING 再失败"
        # 留下误导性的半成品记录。已确认时把 reasons 写进审计 details。
        gate = enforce_license_gate(
            license_name=getattr(source, "license", None),
            access_condition=str(getattr(source, "access_condition", "") or ""),
            is_gated=bool(getattr(source, "is_gated", False)),
            acknowledged=command.license_acknowledged,
        )
        item = DataSourceImport(
            import_id=uuid4(),
            project_id=scope.project_id,
            data_source_id=data_source_id,
            status=ImportStatus.PENDING,
            scope=command.scope,
            tool_name=command.tool_name,
            tool_version=command.tool_version,
            sample_limit=command.sample_limit,
            cache_location=command.cache_location,
            collection_task_id=command.collection_task_id,
            batch_id=command.batch_id,
        )
        created = self._imports.add(scope, item)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_IMPORT,
            resource_id=created.import_id,
            action="create",
            from_status=None,
            to_status=ImportStatus.PENDING.value,
            reason=None,
            request_id=request_id,
            details={
                "data_source_id": str(data_source_id),
                "repo_id": source.repo_id,
                "revision": source.revision,
                "scope": created.scope.value,
                "sample_limit": created.sample_limit,
                # 许可门禁留痕：确认了哪些限制、依据哪个许可。
                "license": gate.license,
                "license_acknowledged": command.license_acknowledged,
                "license_gate_reasons": [reason.value for reason in gate.reasons],
            },
            event_type="datasource.import_requested",
            event_payload={
                "import_id": str(created.import_id),
                "data_source_id": str(data_source_id),
                "repo_id": source.repo_id,
                "revision": source.revision,
                "scope": created.scope.value,
                "sample_limit": created.sample_limit,
                # 让导入 Worker 沿用发起请求的 trace_id；附加字段。
                **({"request_id": request_id} if request_id is not None else {}),
            },
            occurred_at=now,
            # 投递类：worker 据此事真的去下载并解析（R-13 / T-1）。
            dispatch=True,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.import_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_import_action(
        self,
        scope: ProjectScope,
        import_id: UUID,
        action: str,
        *,
        expected_version: int,
        row_count: int | None = None,
        file_count: int | None = None,
        error_message: str | None = None,
        request_id: str | None = None,
    ) -> DataSourceImport:
        # 本方法是 HTTP 用例入口，只接受客户端动作。``retry`` 是 Worker 的
        # 系统动作，只能经 ``ImportRunner.release_for_retry`` 触发；若在入口
        # 不拦截，客户端就能把 FAILED 任务打回 PENDING。默认拒绝，无放行开关。
        action = validate_import_action(action)
        current = self.get_import(scope, import_id)
        if current.row_version != expected_version:
            raise VersionConflictError("导入任务")
        target = import_target_status(current.status, action)
        now = _utcnow()
        updates: dict[str, object] = {
            "status": target,
            "row_version": current.row_version + 1,
        }
        if target is ImportStatus.RUNNING:
            updates["started_at"] = now
        if target in {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}:
            updates["finished_at"] = now
        if row_count is not None:
            updates["imported_row_count"] = row_count
        if file_count is not None:
            updates["imported_file_count"] = file_count
        if target is ImportStatus.FAILED and not (error_message or "").strip():
            raise DomainValidationError("导入失败必须记录 error_message")
        if error_message is not None:
            updates["error_message"] = error_message

        saved = self._imports.save(
            scope, replace(current, **updates), expected_version=expected_version
        )
        common: dict[str, object] = {
            "resource_type": _RESOURCE_IMPORT,
            "resource_id": saved.import_id,
            "action": action,
            "from_status": current.status.value,
            "to_status": saved.status.value,
            "reason": error_message,
            "request_id": request_id,
            "details": {
                "row_count": saved.imported_row_count,
                "file_count": saved.imported_file_count,
            },
            "event_payload": {
                "import_id": str(saved.import_id),
                "data_source_id": str(saved.data_source_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
            },
            "occurred_at": now,
        }
        # 成功事件是**投递类**：与 Worker 路径同一语义，谁宣布导入成功都不能
        # 绕过 Episode 物化（V31-014）。其余动作只留痕。
        if action == "succeed":
            record_change(
                self._audit,
                self._outbox,
                scope,
                **common,
                event_type=DATASOURCE_IMPORT_SUCCEEDED_EVENT,
                dispatch=True,
            )
        else:
            record_change(
                self._audit,
                self._outbox,
                scope,
                **common,
                event_type=DATASOURCE_IMPORT_ACTION_EVENTS[action],
            )
        self._uow.commit()
        return saved

    # ---------- 幂等重放 ----------

    def _replay_source(self, scope: ProjectScope, response_ref: str | None) -> DataSource:
        if not response_ref:
            raise ResourceNotFoundError("数据源", response_ref)
        try:
            return self.get_source(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("数据源", response_ref) from exc

    def _replay_import(self, scope: ProjectScope, response_ref: str | None) -> DataSourceImport:
        if not response_ref:
            raise ResourceNotFoundError("导入任务", response_ref)
        try:
            return self.get_import(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("导入任务", response_ref) from exc


def import_actions(status: str) -> frozenset[str]:
    """当前状态允许的导入动作名；供 API 的 ``allowed_actions`` 出口。"""
    return allowed_import_actions(status)
