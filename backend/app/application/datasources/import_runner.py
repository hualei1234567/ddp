"""导入执行用例：拉取外部文件、校验、落库为可审看的媒体资产。

不在 HTTP 线程执行；由 ``workers/datasources.py`` 在独立进程中调用。

职责边界：
- 下载与解析委托给 ``data-platform/``（纯数据能力）；
- 本层负责事务、状态机推进、审计与 outbox；
- 失败必须落 ``error_message``，不允许"失败了但不知道为什么"。

抽样契约（``scope=SAMPLE``）
---------------------------
``sample_limit`` 此前只写入任务行与事件负载，**执行时从未被读取**，导致
``sample_limit=10`` 的导入实际仍拉全量（Human-1M preview 恒为 15 条）。
规模因此不可控，与 ``docs/ddp/07`` §2「默认只做小样本」的语义相悖。

现在按域模型既有语义执行：``SAMPLE`` 取解析后 Episode 序列的**前 N 个**
（确定性、按元数据出现顺序，不做随机抽样，保证可复现）；``SPLIT`` / ``FULL``
不截断。截断只作用于 Episode 条数，每个被选中 Episode 的**全部机位**都被完整导入，
不会只取部分机位。

同时产出 Canonical 层
--------------------
历史实现只写 ``data_source_media_assets``，**从不创建 ``raw_assets``**，导致导入
产物既不受 RawAsset 的不可变/禁删/去重保护，也无法被 Episode 引用
（``CreateEpisode`` 强制要求 ``raw_asset_ids`` 至少 1 条且资产状态为 VALID）。
主链因此断在「导入 → RawAsset → Episode」这一段。

现在每个被导入的媒体文件同时登记为一条 ``RawAsset``：

- ``source_type=IMPORT``（该枚举值设计预留但此前从未使用）；
- 对象键改用 Canonical 规范 ``raw/{project_id}/{yyyy}/{mm}/{dd}/{asset_id}/content.{ext}``，
  不再使用 ``data-sources/...`` 私有前缀；
- 生命周期按域规则走 ``VALIDATING -> mark_valid -> VALID``（``mark_valid`` 记录
  ``verified_at``）——内容签名与 sha256 已在 ``Fetcher`` 内部校验通过，这里的
  ``VALID`` 是有据可依的结论，不是默认成功；
- 同项目内相同内容（sha256 + size_bytes 相同）复用既有可用资产，不重复写对象，
  与上传链路的去重语义一致；
- 来源信息（数据源、导入任务、revision、原始路径、机位、任务名）写入
  ``RawAsset.raw_metadata``，这是当前 DataSource → RawAsset 唯一可追溯的载体。
  ``raw_assets`` 表没有 ``data_source_id`` 列，加列属于跨阶段契约变更，不在本次范围。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.datasources.events import (
    DATASOURCE_IMPORT_ACTION_EVENTS,
    DATASOURCE_IMPORT_SUCCEEDED_EVENT,
)
from app.application.datasources.observability import sanitize_log_message
from app.application.datasources.ports import (
    AuditRepository,
    DataSourceImportLogRepository,
    DataSourceImportRepository,
    DataSourceRepository,
    MediaAssetRepository,
    ObjectWriter,
    OutboxRepository,
    UnitOfWork,
)
from app.application.ingestion.ports import RawAssetRepository
from app.core.logging import current_trace_id
from app.domain.datasources.enums import ImportLogLevel, ImportScope, ImportStage, ImportStatus
from app.domain.datasources.errors import DomainValidationError
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSourceImport, DataSourceImportLog
from app.domain.datasources.state_machines import import_target_status
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
)
from app.domain.ingestion.errors import ConflictError
from app.domain.ingestion.models import RawAsset
from app.domain.ingestion.validation import (
    HDF5_DETECTION_BYTES,
    build_raw_object_key,
    canonical_content_type,
    detect_format,
)

# Human-1M preview 的固定入口。不遍历目录：HF 没有廉价列目录能力，
# 且遍历会带来"下多少算多少"的不确定行为。
PREVIEW_METADATA_PATH = "preview/metadata.jsonl"

# 每处理 N 个媒体文件提交一次进度，而不是每个文件一次事务。
# 进度是给运维看的，10 条的粒度足够；全量导入动辄上千文件，逐条提交会把
# 一次导入变成上千次事务。阶段切换与结束必定写一次，不依赖这个阈值。
_PROGRESS_COMMIT_EVERY = 10

# 声明的内容类型 → RawAsset 格式兜底。只覆盖导入链路会遇到的类型；
# 未登记的类型一律落到 UNKNOWN，不猜。
_FORMAT_BY_CONTENT_TYPE: dict[str, RawAssetFormat] = {
    "video/mp4": RawAssetFormat.MP4,
    "video/quicktime": RawAssetFormat.MP4,
    "application/vnd.apache.parquet": RawAssetFormat.PARQUET,
    "application/json": RawAssetFormat.JSON,
    "application/x-ndjson": RawAssetFormat.JSON,
    "text/csv": RawAssetFormat.CSV,
    "application/zip": RawAssetFormat.ZIP,
}


class FetchedFileT(Protocol):
    data: bytes
    size_bytes: int
    sha256: str
    content_type: str


class Fetcher(Protocol):
    """按 repo/revision/path 取文件；实现负责 HTTP 与内容签名校验。"""

    def __call__(self, *, repo_id: str, revision: str, path: str) -> FetchedFileT: ...


MetadataParser = Callable[[list[dict[str, object]]], tuple["ImportEpisode", ...]]


class ImportEpisode(Protocol):
    """解析器产出的 Episode 形状；只声明本模块实际读取的字段。"""

    episode_key: str
    task_name: str
    instruction: str
    media_assets: tuple[ImportMediaAsset, ...]


class ImportMediaAsset(Protocol):
    """解析器产出的机位形状；只声明本模块实际读取的字段。"""

    path: str
    camera: str
    excerpt_start_seconds: float | None
    excerpt_seconds: float | None
    source_fps: float | None


class ImportSource(Protocol):
    """数据源在本模块用到的字段。"""

    data_source_id: UUID
    repo_id: str
    revision: str


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    import_id: UUID
    episode_count: int
    asset_count: int
    total_bytes: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def select_episodes(
    episodes: tuple[ImportEpisode, ...],
    *,
    scope: ImportScope | str,
    sample_limit: int | None,
) -> tuple[ImportEpisode, ...]:
    """按导入范围选取要真正下载的 Episode。

    - ``SAMPLE``：取前 ``sample_limit`` 个（元数据顺序，确定性，可复现）。
      ``sample_limit`` 超出实际条数时取全部，不报错——数据量本身可能少于预期，
      这不是错误；真正的"空数据"由解析器先行拒绝。
    - ``SPLIT`` / ``FULL``：不截断。

    抽样单位是 **Episode**，不是视频文件：调用方随后会遍历每个 Episode 的
    全部机位，因此不会出现"某条 episode 只导了一半机位"的残缺数据。
    """
    kind = ImportScope(scope)
    if kind is not ImportScope.SAMPLE:
        return episodes
    if sample_limit is None:
        # 域模型已保证 SAMPLE 必须有 limit；这里兜底失败而不是静默全量。
        raise DomainValidationError("scope=SAMPLE 时必须给出 sample_limit")
    if sample_limit < 1:
        raise DomainValidationError("sample_limit 必须大于 0")
    return episodes[:sample_limit]


class ImportRunner:
    """执行一次导入。调用方负责事件抢占与重试策略。"""

    def __init__(
        self,
        sources: DataSourceRepository,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        raw_assets: RawAssetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        storage: ObjectWriter,
        uow: UnitOfWork,
        logs: DataSourceImportLogRepository,
    ) -> None:
        self._sources = sources
        self._imports = imports
        self._assets = assets
        self._raw_assets = raw_assets
        self._audit = audit
        self._outbox = outbox
        self._storage = storage
        self._uow = uow
        self._logs = logs

    def execute(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        fetch: Fetcher,
        parse_metadata: Callable[[bytes], list[dict[str, object]]],
        parse_episodes: MetadataParser,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ImportOutcome:
        # ``now`` 是**开始**时刻（可注入，便于确定性测试）；结束时刻必须重新
        # 取一次时钟，否则 started_at 与 finished_at 永远相等，导入耗时恒为 0。
        # ``clock`` 让测试能验证"两次取时间确实不同"，而不必依赖真实等待。
        tick = clock or _utcnow
        moment = now or tick()
        job = self._imports.get(scope, import_id)
        if job is None:
            raise DomainValidationError("导入任务不存在或不属于当前项目")
        if job.status is not ImportStatus.PENDING:
            # 幂等保护：重复投递不应重复执行。
            raise DomainValidationError(f"导入任务当前状态 {job.status} 不允许执行")

        self._advance(scope, job, "start", moment)
        running = self._imports.get(scope, import_id)
        assert running is not None  # 刚写入，必然可见

        # 进入下载阶段：只声明"开始做什么"，不填总条数——元数据还没解析，
        # total_items 未知就是 NULL（未测量），不伪造分母。
        running = self._report(
            scope,
            running,
            stage=ImportStage.DOWNLOAD,
            processed_items=0,
            total_items=None,
            message="开始导入：准备下载数据集元数据",
            moment=moment,
        )

        source = self._sources.get(scope, running.data_source_id)
        if source is None:
            self._fail(scope, running, "数据源已被删除", max(tick(), moment))
            raise DomainValidationError("数据源不存在")

        total_bytes = 0
        try:
            meta = fetch(
                repo_id=source.repo_id,
                revision=source.revision,
                path=PREVIEW_METADATA_PATH,
            )
            rows = parse_metadata(meta.data)
            running = self._report(
                scope,
                running,
                stage=ImportStage.VALIDATE,
                processed_items=0,
                total_items=None,
                message=f"元数据校验通过：{len(rows)} 行",
                moment=max(tick(), moment),
            )
            parsed = parse_episodes(rows)
            episodes = select_episodes(
                parsed,
                scope=running.scope,
                sample_limit=running.sample_limit,
            )
            # 解析完成后才知道总条数：单位是媒体文件（与 imported_file_count 同口径）。
            total_items = sum(len(episode.media_assets) for episode in episodes)
            running = self._report(
                scope,
                running,
                stage=ImportStage.PARSE,
                processed_items=0,
                total_items=total_items,
                message=f"解析出 {len(episodes)} 个 episode，共 {total_items} 个媒体文件",
                moment=max(tick(), moment),
            )
            running = self._report(
                scope,
                running,
                stage=ImportStage.INGEST,
                processed_items=0,
                total_items=total_items,
                message=f"开始入库：{total_items} 个媒体文件",
                moment=max(tick(), moment),
            )

            asset_count = 0
            for episode in episodes:
                for asset in episode.media_assets:
                    # 进度提交会把已完成资产一并落库；重试时先查已落库资产，
                    # 已存在就跳过下载与写入。否则同 (import_id, path) 会撞
                    # 唯一约束，一次可恢复失败会变成永久失败。
                    existing = self._assets.get_by_import_path(scope, running.import_id, asset.path)
                    if existing is None:
                        fetched = fetch(
                            repo_id=source.repo_id,
                            revision=source.revision,
                            path=f"preview/{asset.path}",
                        )
                        raw_asset, written = self._register_raw_asset(
                            scope,
                            source,
                            running,
                            episode,
                            asset,
                            fetched,
                            moment=moment,
                        )
                        if written:
                            total_bytes += fetched.size_bytes
                        self._assets.add(
                            scope,
                            MediaAsset(
                                asset_id=uuid4(),
                                project_id=scope.project_id,
                                data_source_id=source.data_source_id,
                                import_id=running.import_id,
                                episode_key=episode.episode_key,
                                path=asset.path,
                                camera=asset.camera,
                                # 内容复用时指向既有对象的键，否则播放会指向不存在的对象。
                                object_key=raw_asset.object_key,
                                size_bytes=fetched.size_bytes,
                                sha256=fetched.sha256,
                                content_type=fetched.content_type,
                                task_name=episode.task_name,
                                raw_asset_id=raw_asset.asset_id,
                                excerpt_start_seconds=asset.excerpt_start_seconds,
                                excerpt_seconds=asset.excerpt_seconds,
                                source_fps=asset.source_fps,
                                created_at=moment,
                            ),
                        )
                    asset_count += 1
                    if asset_count % _PROGRESS_COMMIT_EVERY == 0:
                        running = self._report(
                            scope,
                            running,
                            stage=ImportStage.INGEST,
                            processed_items=asset_count,
                            total_items=total_items,
                            message=f"入库进度：{asset_count}/{total_items}",
                            moment=max(tick(), moment),
                        )

            finished = max(tick(), moment)
            running = self._report(
                scope,
                running,
                stage=ImportStage.COMPLETE,
                processed_items=asset_count,
                total_items=total_items,
                message=f"导入完成：{len(episodes)} 个 episode / {asset_count} 个媒体文件",
                moment=finished,
            )
            # row_count 记录**实际导入**的 episode 数（抽样后），
            # 与 imported_file_count（资产数）口径一致，避免"报告 15 实际 10"。
            self._advance(
                scope,
                running,
                "succeed",
                finished,
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
                self._fail(scope, latest, str(exc), max(tick(), moment))
            raise

    def release_for_retry(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        moment: datetime | None = None,
    ) -> bool:
        """把一次**可重试**失败退回 ``PENDING``，让下一轮事件重新领取。

        为什么不能直接留在 ``FAILED``：契约把 ``FAILED`` 定为终态（终态之后
        任何客户端动作返回 409），而 Worker 在失败时已把 outbox 事件
        ``mark_retry`` 排到下一轮。下一轮 ``execute`` 要求状态为 ``PENDING``，
        若停在 ``FAILED``，这条导入就永久卡死、事件空转到死信——没有任何
        客户端 API 能救。

        返回 ``True`` 表示已退回可重试；``False`` 表示已达 ``max_attempts``，
        保持 ``FAILED`` 终态（此时确实该人工介入，不该无限重试）。
        """
        now = moment or _utcnow()
        latest = self._imports.get(scope, import_id)
        if latest is None or latest.status is not ImportStatus.FAILED:
            return False
        if not latest.can_retry:
            return False
        target = import_target_status(latest.status, "retry")
        saved = self._imports.save(
            scope,
            replace(
                latest,
                status=target,
                attempt=latest.attempt + 1,
                started_at=None,
                finished_at=None,
                error_message=None,
                # 退回待领取后，上一轮的阶段/进度不再代表当前状态：
                # 清成"未测量"，由下一次 execute 重新写真实阶段。
                stage=None,
                processed_items=None,
                total_items=None,
                row_version=latest.row_version + 1,
            ),
            expected_version=latest.row_version,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="data_source_import",
            resource_id=saved.import_id,
            action="retry",
            from_status=latest.status.value,
            to_status=saved.status.value,
            reason="系统重试：可恢复失败，退回待领取",
            request_id=current_trace_id(),
            details={"attempt": saved.attempt, "max_attempts": saved.max_attempts},
            event_type="datasource.import.retry",
            event_payload={
                "import_id": str(saved.import_id),
                "attempt": saved.attempt,
                "max_attempts": saved.max_attempts,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return True

    def _report(
        self,
        scope: ProjectScope,
        job: DataSourceImport,
        *,
        stage: ImportStage,
        processed_items: int | None,
        total_items: int | None,
        message: str,
        moment: datetime,
        level: ImportLogLevel = ImportLogLevel.INFO,
    ) -> DataSourceImport:
        """写一次阶段/进度并追加上一条步骤日志，然后提交。

        为什么不走 ``record_change``：审计记录的是业务变更（谁把状态改成了
        什么），运行期进度是高频遥测；写进 ``audit_logs`` 会污染变更时间线，
        也无法表达 ``stage`` / ``level``。因此进度落在导入任务行上，步骤落在
        ``data_source_import_logs``。

        必须提交：API 用另一个会话读取，未提交的进度前端看不到。提交也会把
        本轮已完成的媒体资产一并落库，所以 ingest 必须按 ``(import_id, path)``
        幂等（见 ``execute`` 里的 ``get_by_import_path``）。
        """
        saved = self._imports.save(
            scope,
            replace(
                job,
                stage=stage,
                processed_items=processed_items,
                total_items=total_items,
                row_version=job.row_version + 1,
            ),
            expected_version=job.row_version,
        )
        self._logs.add(
            scope,
            DataSourceImportLog(
                log_id=uuid4(),
                project_id=scope.project_id,
                import_id=saved.import_id,
                stage=stage,
                level=level,
                message=sanitize_log_message(message),
                occurred_at=moment,
                sequence_no=saved.row_version,
                attempt=job.attempt,
            ),
        )
        self._uow.commit()
        return saved

    def _register_raw_asset(
        self,
        scope: ProjectScope,
        source: ImportSource,
        job: DataSourceImport,
        episode: ImportEpisode,
        media: ImportMediaAsset,
        fetched: FetchedFileT,
        *,
        moment: datetime,
    ) -> tuple[RawAsset, bool]:
        """把已下载的媒体文件登记为 Canonical 层 RawAsset。

        返回 ``(资产, 是否新写入了对象)``。同项目内相同内容已存在时复用既有
        资产且**不重复写对象**——与上传链路 ``find_active_by_content`` 的去重
        语义一致，避免同一份视频在对象存储里存两遍。
        """
        sha256 = fetched.sha256
        size_bytes = fetched.size_bytes
        existing = self._raw_assets.find_active_by_content(
            scope, sha256=sha256, size_bytes=size_bytes
        )
        if existing is not None:
            return existing, False

        asset_id = uuid4()
        raw_format = _detect_raw_format(data=fetched.data, content_type=fetched.content_type)
        object_key = build_raw_object_key(scope.project_id, asset_id, moment, raw_format)
        self._storage.put(object_key, fetched.data, content_type=fetched.content_type)
        asset = RawAsset(
            asset_id=asset_id,
            project_id=scope.project_id,
            code=f"RAW-{asset_id.hex[:20]}",
            source_type=RawAssetSourceType.IMPORT,
            original_filename=_safe_filename(media.path),
            object_key=object_key,
            size_bytes=size_bytes,
            content_type=canonical_content_type(raw_format),
            format=raw_format,
            sha256=sha256,
            status=RawAssetStatus.VALIDATING,
            ingested_at=moment,
            storage_class=StorageClass.STANDARD,
            uploaded_by=scope.actor_id,
            # 继承导入记录的归属：数据是为哪个采集任务/批次采的，资产上要能查到，
            # 否则下游按任务统计产出永远是 0。
            collection_task_id=job.collection_task_id,
            batch_id=job.batch_id,
            raw_metadata=_import_metadata(source, job, episode, media),
            created_at=moment,
            updated_at=moment,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        try:
            created = self._raw_assets.add(scope, asset)
        except ConflictError:
            # 并发导入同一份内容：以先写入者为准，本次不再重复写对象。
            winner = self._raw_assets.find_active_by_content(
                scope, sha256=sha256, size_bytes=size_bytes
            )
            if winner is None:
                raise
            return winner, True
        # 内容签名与 sha256 已由 Fetcher 校验通过，VALID 有据可依。
        verified = created.mark_valid(now=moment)
        saved = self._raw_assets.save(scope, verified, expected_version=created.version)
        return saved, True

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
        event_payload = {
            "import_id": str(saved.import_id),
            "data_source_id": str(saved.data_source_id),
            "to_status": saved.status.value,
        }
        common: dict[str, object] = {
            "resource_type": "data_source_import",
            "resource_id": saved.import_id,
            "action": action,
            "from_status": job.status.value,
            "to_status": saved.status.value,
            "reason": None,
            "request_id": current_trace_id(),
            "details": {"row_count": row_count, "file_count": file_count},
            "event_payload": event_payload,
            "occurred_at": moment,
        }
        if action == "succeed":
            # 投递类：Worker 消费后自动物化 Episode（V31-014）。显式
            # ``dispatch=True`` 同时是 ``scripts/check_outbox_events.py``
            # 静态识别的构造点，其余动作仍是留痕类。
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

    def _fail(
        self, scope: ProjectScope, job: DataSourceImport, message: str, moment: datetime
    ) -> None:
        target = import_target_status(job.status, "fail")
        # 异常文本可能带上游 URL、预签名参数或本机绝对路径；错误详情要展示
        # 给前端，因此与步骤日志用同一套脱敏，不留后门。
        safe_message = sanitize_log_message(message, limit=2000)
        saved = self._imports.save(
            scope,
            replace(
                job,
                status=target,
                error_message=safe_message,
                finished_at=moment,
                row_version=job.row_version + 1,
            ),
            expected_version=job.row_version,
        )
        self._logs.add(
            scope,
            DataSourceImportLog(
                log_id=uuid4(),
                project_id=scope.project_id,
                import_id=saved.import_id,
                # 失败发生在当前阶段；若尚未进入任何阶段（极端情况），
                # 归到 DOWNLOAD——这是执行前正要做的第一步。
                stage=job.stage or ImportStage.DOWNLOAD,
                level=ImportLogLevel.ERROR,
                # 日志列上限 1000，错误详情列上限 2000；两者分别截断。
                message=sanitize_log_message(message),
                occurred_at=moment,
                sequence_no=saved.row_version,
                attempt=job.attempt,
            ),
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
            reason=safe_message,
            request_id=current_trace_id(),
            details={},
            event_type="datasource.import.fail",
            event_payload={"import_id": str(saved.import_id), "error_message": safe_message},
            occurred_at=moment,
        )
        self._uow.commit()


def _detect_raw_format(*, data: bytes, content_type: str) -> RawAssetFormat:
    """按内容签名判定 RawAsset 格式；探测不出时按声明的内容类型兜底。

    ``Fetcher`` 已校验「内容签名与扩展名一致」，所以这里的兜底不会把
    改名的文件放行。真正的兜底链是：内容签名 → 声明类型 → UNKNOWN。
    """
    detected = detect_format(data[:HDF5_DETECTION_BYTES], data[-4:])
    if detected is not None:
        return detected
    declared = _FORMAT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
    return declared if declared is not None else RawAssetFormat.UNKNOWN


def _safe_filename(path: str) -> str:
    """从导入路径取展示文件名；路径本身不可信，只取最后一段。"""
    return path.rsplit("/", 1)[-1] or "asset"


def _import_metadata(
    source: ImportSource,
    job: DataSourceImport,
    episode: ImportEpisode,
    media: ImportMediaAsset,
) -> dict[str, object]:
    """DataSource → RawAsset 的来源留痕。

    ``raw_assets`` 表没有 ``data_source_id`` 列（加列属跨阶段契约变更），
    因此来源链只能靠 ``raw_metadata`` 承载：拿到一条 RawAsset，就能回溯到
    哪个数据源、哪次导入、哪个 revision、原始路径与机位。
    """
    return {
        "origin": "data_source_import",
        "data_source_id": str(source.data_source_id),
        "repo_id": source.repo_id,
        "revision": source.revision,
        "import_id": str(job.import_id),
        "episode_key": episode.episode_key,
        "source_path": media.path,
        "camera": media.camera,
        # 声明帧率是「帧数」的唯一可用依据（平台不解码视频）。MediaAsset 上有列，
        # RawAsset 没有；Episode 自动就绪只认 Canonical 事实，因此必须在这里
        # 留一份，否则生成 Manifest 时永远推不出 frame_count。
        "source_fps": media.source_fps,
        "task_name": episode.task_name,
        # 自然语言指令是 Canonical Episode 的必备语义字段，DataSource/MediaAsset
        # 都没有对应的列；不在这里留一份，后续生成 Episode 时就永久丢掉了。
        "instruction": episode.instruction,
    }
