"""导入执行用例：拉取外部文件、校验、落库为可审看的媒体资产。

不在 HTTP 线程执行；由 ``workers/datasources.py`` 在独立进程中调用。

职责边界：
- 下载与解析委托给 ``data-platform/``（纯数据能力）；
- 本层负责事务、状态机推进、审计与 outbox；
- 失败必须落 ``error_message``，不允许"失败了但不知道为什么"。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.datasources.ports import (
    AuditRepository,
    DataSourceImportRepository,
    DataSourceRepository,
    MediaAssetRepository,
    ObjectWriter,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import DomainValidationError
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSourceImport
from app.domain.datasources.state_machines import import_target_status
from app.domain.identity.models import ProjectScope

# Human-1M preview 的固定入口。不遍历目录：HF 没有廉价列目录能力，
# 且遍历会带来"下多少算多少"的不确定行为。
PREVIEW_METADATA_PATH = "preview/metadata.jsonl"


class FetchedFileT(Protocol):
    data: bytes
    size_bytes: int
    sha256: str
    content_type: str


class Fetcher(Protocol):
    """按 repo/revision/path 取文件；实现负责 HTTP 与内容签名校验。"""

    def __call__(self, *, repo_id: str, revision: str, path: str) -> FetchedFileT: ...


MetadataParser = Callable[[list[dict[str, object]]], tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    import_id: UUID
    episode_count: int
    asset_count: int
    total_bytes: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ImportRunner:
    """执行一次导入。调用方负责事件抢占与重试策略。"""

    def __init__(
        self,
        sources: DataSourceRepository,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        storage: ObjectWriter,
        uow: UnitOfWork,
    ) -> None:
        self._sources = sources
        self._imports = imports
        self._assets = assets
        self._audit = audit
        self._outbox = outbox
        self._storage = storage
        self._uow = uow

    def execute(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        fetch: Fetcher,
        parse_metadata: Callable[[bytes], list[dict[str, object]]],
        parse_episodes: MetadataParser,
        now: datetime | None = None,
    ) -> ImportOutcome:
        moment = now or _utcnow()
        job = self._imports.get(scope, import_id)
        if job is None:
            raise DomainValidationError("导入任务不存在或不属于当前项目")
        if job.status is not ImportStatus.PENDING:
            # 幂等保护：重复投递不应重复执行。
            raise DomainValidationError(f"导入任务当前状态 {job.status} 不允许执行")

        self._advance(scope, job, "start", moment)
        running = self._imports.get(scope, import_id)
        assert running is not None  # 刚写入，必然可见

        source = self._sources.get(scope, running.data_source_id)
        if source is None:
            self._fail(scope, running, "数据源已被删除", moment)
            raise DomainValidationError("数据源不存在")

        total_bytes = 0
        try:
            meta = fetch(
                repo_id=source.repo_id,
                revision=source.revision,
                path=PREVIEW_METADATA_PATH,
            )
            episodes = parse_episodes(parse_metadata(meta.data))

            asset_count = 0
            for episode in episodes:
                for asset in episode.media_assets:  # type: ignore[attr-defined]
                    fetched = fetch(
                        repo_id=source.repo_id,
                        revision=source.revision,
                        path=f"preview/{asset.path}",  # type: ignore[attr-defined]
                    )
                    object_key = _object_key(
                        scope,
                        source.data_source_id,
                        source.revision,
                        asset.path,  # type: ignore[attr-defined]
                    )
                    self._storage.put(object_key, fetched.data, content_type=fetched.content_type)
                    self._assets.add(
                        scope,
                        MediaAsset(
                            asset_id=uuid4(),
                            project_id=scope.project_id,
                            data_source_id=source.data_source_id,
                            import_id=running.import_id,
                            episode_key=episode.episode_key,  # type: ignore[attr-defined]
                            path=asset.path,  # type: ignore[attr-defined]
                            camera=asset.camera,  # type: ignore[attr-defined]
                            object_key=object_key,
                            size_bytes=fetched.size_bytes,
                            sha256=fetched.sha256,
                            content_type=fetched.content_type,
                            task_name=episode.task_name,  # type: ignore[attr-defined]
                            excerpt_start_seconds=asset.excerpt_start_seconds,  # type: ignore[attr-defined]
                            excerpt_seconds=asset.excerpt_seconds,  # type: ignore[attr-defined]
                            source_fps=asset.source_fps,  # type: ignore[attr-defined]
                            created_at=moment,
                        ),
                    )
                    total_bytes += fetched.size_bytes
                    asset_count += 1

            self._advance(
                scope,
                running,
                "succeed",
                moment,
                row_count=len(episodes),
                file_count=asset_count,
            )
            return ImportOutcome(
                import_id=import_id,
                episode_count=len(episodes),
                asset_count=asset_count,
                total_bytes=total_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - 任何失败都必须落库留痕
            latest = self._imports.get(scope, import_id)
            if latest is not None and latest.status is ImportStatus.RUNNING:
                self._fail(scope, latest, str(exc)[:2000], moment)
            raise

    def _advance(
        self,
        scope: ProjectScope,
        job: DataSourceImport,
        action: str,
        moment: datetime,
        *,
        row_count: int | None = None,
        file_count: int | None = None,
    ) -> DataSourceImport:
        target = import_target_status(job.status, action)
        updates: dict[str, object] = {"status": target, "row_version": job.row_version + 1}
        if target is ImportStatus.RUNNING:
            updates["started_at"] = moment
        if target in {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}:
            updates["finished_at"] = moment
        if row_count is not None:
            updates["imported_row_count"] = row_count
        if file_count is not None:
            updates["imported_file_count"] = file_count
        saved = self._imports.save(scope, replace(job, **updates), expected_version=job.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="data_source_import",
            resource_id=saved.import_id,
            action=action,
            from_status=job.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=None,
            details={"row_count": row_count, "file_count": file_count},
            event_type=f"datasource.import.{action}",
            event_payload={
                "import_id": str(saved.import_id),
                "data_source_id": str(saved.data_source_id),
                "to_status": saved.status.value,
            },
            occurred_at=moment,
        )
        self._uow.commit()
        return saved

    def _fail(
        self, scope: ProjectScope, job: DataSourceImport, message: str, moment: datetime
    ) -> None:
        target = import_target_status(job.status, "fail")
        saved = self._imports.save(
            scope,
            replace(
                job,
                status=target,
                error_message=message,
                finished_at=moment,
                row_version=job.row_version + 1,
            ),
            expected_version=job.row_version,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="data_source_import",
            resource_id=saved.import_id,
            action="fail",
            from_status=job.status.value,
            to_status=saved.status.value,
            reason=message,
            request_id=None,
            details={},
            event_type="datasource.import.fail",
            event_payload={"import_id": str(saved.import_id), "error_message": message},
            occurred_at=moment,
        )
        self._uow.commit()


def _object_key(scope: ProjectScope, data_source_id: UUID, revision: str, path: str) -> str:
    """对象键包含 revision：同一数据源换版本不会覆盖旧对象。"""
    return f"data-sources/{scope.project_id}/{data_source_id}/{revision}/{path}"
