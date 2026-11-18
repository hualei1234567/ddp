"""数据源应用层端口。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.application.audit import AuditRepository as _AuditRepository
from app.application.audit import OutboxRepository as _OutboxRepository
from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.datasources.media import MediaAsset as MediaAssetT
from app.domain.datasources.models import DataSource, DataSourceImport, DataSourceImportLog
from app.domain.datasources.review import DataReview as DataReviewT
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class DataSourceRepository(Protocol):
    def add(self, scope: ProjectScope, item: DataSource) -> DataSource: ...

    def get(self, scope: ProjectScope, data_source_id: UUID) -> DataSource | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> DataSource | None: ...

    def get_by_revision(
        self,
        scope: ProjectScope,
        *,
        repo_id: str,
        revision: str,
        config_name: str | None,
        split: str | None,
    ) -> DataSource | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSource]: ...

    def save(
        self,
        scope: ProjectScope,
        item: DataSource,
        *,
        expected_version: int,
    ) -> DataSource: ...


class DataSourceImportRepository(Protocol):
    def add(self, scope: ProjectScope, item: DataSourceImport) -> DataSourceImport: ...

    def get(self, scope: ProjectScope, import_id: UUID) -> DataSourceImport | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        data_source_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSourceImport]: ...

    def save(
        self,
        scope: ProjectScope,
        item: DataSourceImport,
        *,
        expected_version: int,
    ) -> DataSourceImport: ...

    def list_for_collection_task(
        self,
        scope: ProjectScope,
        collection_task_id: UUID,
        *,
        limit: int = 100,
    ) -> list[DataSourceImport]:
        """该采集任务名下的全部导入（迁移 0050 的归属列）。"""
        ...


class DataReviewRepository(Protocol):
    def add(self, scope: ProjectScope, review: DataReviewT) -> DataReviewT: ...

    def get_by_import(self, scope: ProjectScope, import_id: UUID) -> DataReviewT | None: ...

    def save(
        self, scope: ProjectScope, review: DataReviewT, *, expected_version: int
    ) -> DataReviewT: ...


class ObjectReader(Protocol):
    """签发临时只读访问；实现负责签名，不把存储凭据交给前端。"""

    def presign_get(self, object_key: str, *, expires_seconds: int) -> str: ...


class ObjectWriter(Protocol):
    """对象存储写入端口；实现不得在此层做业务校验。"""

    def put(self, object_key: str, data: bytes, *, content_type: str) -> None: ...


class MediaAssetRepository(Protocol):
    def add(self, scope: ProjectScope, asset: MediaAssetT) -> MediaAssetT: ...

    def get_by_import_path(
        self, scope: ProjectScope, import_id: UUID, path: str
    ) -> MediaAssetT | None:
        """按 ``(import_id, path)`` 查已落库资产。

        Worker 在入库阶段会分批提交进度；重试时已提交的资产必须能跳过，
        否则同一路径会撞唯一约束，把"可重试"变成"永久失败"。
        """
        ...

    def list_by_import(
        self, scope: ProjectScope, import_id: UUID, *, limit: int = 200
    ) -> list[MediaAssetT]: ...

    def count_by_import(self, scope: ProjectScope, import_id: UUID) -> int:
        """按项目统计一次导入的媒体资产数。

        统一质量引擎用它判断是否超过检查上限：只取前 N 条再宣称"全部通过"
        属于静默截断，必须先知道总数再决定拒绝还是执行。
        """
        ...

    def list_by_raw_asset_ids(
        self, scope: ProjectScope, raw_asset_ids: tuple[UUID, ...]
    ) -> list[MediaAssetT]:
        """按 Canonical 层 RawAsset ID 反查导入产出的媒体资产。

        Episode 与导入资产之间没有直接外键：Episode 引用 RawAsset，
        导入媒体资产再引用同一个 RawAsset。统一质检需要这条只读连接。
        """
        ...


class DataSourceImportLogRepository(Protocol):
    """导入步骤日志：只追加、按项目隔离、只读列表。"""

    def add(self, scope: ProjectScope, item: DataSourceImportLog) -> DataSourceImportLog: ...

    def list_recent(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        limit: int = 100,
        before_log_id: UUID | None = None,
    ) -> tuple[list[DataSourceImportLog], bool]:
        """返回按时间正序的最近 ``limit`` 条，以及是否还有更早的日志。

        ``before_log_id`` 为上一页最旧一条日志的 ID：传入时只返回严格早于
        该条目的日志（仍按时间正序），用于加载更早的历史。游标不属于本次
        导入（或已被删除）时返回空页，不泄露其他导入的日志。
        """
        ...


# 审计、outbox 与幂等记录类型统一由 application 顶层声明。
AuditRecord = object
OutboxEvent = object
AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository

__all__ = [
    "AuditRepository",
    "DataReviewRepository",
    "MediaAssetRepository",
    "ObjectReader",
    "ObjectWriter",
    "DataSourceImportLogRepository",
    "DataSourceImportRepository",
    "DataSourceRepository",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "OutboxRepository",
    "UnitOfWork",
]
