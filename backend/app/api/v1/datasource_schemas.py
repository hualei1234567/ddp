"""数据源域请求/响应模型。

``extra="forbid"``：未知字段直接 422，避免客户端以为传了却静默忽略。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.datasources.observability import sanitize_log_message
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSource, DataSourceImport, DataSourceImportLog
from app.domain.datasources.review import DataReview as DataReviewT
from app.domain.datasources.state_machines import allowed_import_actions


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    repo_id: str = Field(min_length=3, max_length=200)
    # 必须是 commit sha：契约层就拒绝 main/分支名。
    revision: str = Field(min_length=40, max_length=40, pattern="^[0-9a-f]{40}$")
    license: str = Field(min_length=1, max_length=100)
    access_condition: str = Field(pattern="^(PUBLIC|GATED|PRIVATE|UNKNOWN)$")
    is_gated: bool = False
    kind: str = Field(default="HUGGINGFACE", pattern="^HUGGINGFACE$")
    config_name: str | None = Field(default=None, max_length=100)
    split: str | None = Field(default=None, max_length=100)
    dataset_card_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] | None = None
    # 显式请求远端校验；默认关闭，避免每次登记都访问外网。
    verify_remote: bool = False


class DataSourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    license: str | None = Field(default=None, min_length=1, max_length=100)
    access_condition: str | None = Field(default=None, pattern="^(PUBLIC|GATED|PRIVATE|UNKNOWN)$")
    is_gated: bool | None = None
    config_name: str | None = Field(default=None, max_length=100)
    split: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class DataSourceOut(BaseModel):
    data_source_id: UUID
    project_id: UUID
    code: str
    name: str
    kind: str
    repo_id: str
    revision: str
    license: str
    access_condition: str
    is_gated: bool
    config_name: str | None
    split: str | None
    dataset_card_url: str | None
    notes: str | None
    metadata: dict[str, Any]
    version: int

    @classmethod
    def from_domain(cls, item: DataSource) -> DataSourceOut:
        return cls(
            data_source_id=item.data_source_id,
            project_id=item.project_id,
            code=item.code,
            name=item.name,
            kind=str(item.kind),
            repo_id=item.repo_id,
            revision=item.revision,
            license=item.license,
            access_condition=str(item.access_condition),
            is_gated=item.is_gated,
            config_name=item.config_name,
            split=item.split,
            dataset_card_url=item.dataset_card_url,
            notes=item.notes,
            metadata=dict(item.metadata),
            version=item.row_version,
        )


class DataSourceImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(pattern="^(SAMPLE|SPLIT|FULL)$")
    tool_name: str = Field(min_length=1, max_length=200)
    tool_version: str = Field(min_length=1, max_length=100)
    sample_limit: int | None = Field(default=None, ge=1)
    cache_location: str | None = Field(default=None, max_length=1024)
    # 数据归属：这批数据是为哪个采集任务/批次采的。导入产出的 RawAsset 与
    # 物化出的 Episode 会继承它。
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    #: V61-023 许可门禁：GATED 或非宽松许可的数据源必须显式确认。
    #: 未确认时返回 422 并说明原因；宽松许可的数据源无需传本字段。
    license_acknowledged: bool = False


class ImportActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_count: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    error_message: str | None = Field(default=None, max_length=2000)


class DataSourceImportOut(BaseModel):
    import_id: UUID
    project_id: UUID
    data_source_id: UUID
    status: str
    scope: str
    tool_name: str
    tool_version: str
    sample_limit: int | None
    cache_location: str | None
    collection_task_id: UUID | None
    batch_id: UUID | None
    imported_row_count: int | None
    imported_file_count: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    # 运行期可观测性：null 表示"未测量"（迁移前的存量导入），不是 0%。
    stage: str | None
    processed_items: int | None
    total_items: int | None
    version: int
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, item: DataSourceImport) -> DataSourceImportOut:
        return cls(
            import_id=item.import_id,
            project_id=item.project_id,
            data_source_id=item.data_source_id,
            status=str(item.status),
            scope=str(item.scope),
            tool_name=item.tool_name,
            tool_version=item.tool_version,
            sample_limit=item.sample_limit,
            cache_location=item.cache_location,
            collection_task_id=item.collection_task_id,
            batch_id=item.batch_id,
            imported_row_count=item.imported_row_count,
            imported_file_count=item.imported_file_count,
            error_message=item.error_message,
            started_at=item.started_at,
            finished_at=item.finished_at,
            stage=str(item.stage) if item.stage is not None else None,
            processed_items=item.processed_items,
            total_items=item.total_items,
            version=item.row_version,
            allowed_actions=sorted(allowed_import_actions(item.status)),
        )


class ImportLogOut(BaseModel):
    """一条导入步骤日志（脱敏后的只读投影）。"""

    log_id: UUID
    project_id: UUID
    import_id: UUID
    stage: str
    level: str
    message: str
    attempt: int
    occurred_at: datetime

    @classmethod
    def from_domain(cls, item: DataSourceImportLog) -> ImportLogOut:
        return cls(
            log_id=item.log_id,
            project_id=item.project_id,
            import_id=item.import_id,
            stage=str(item.stage),
            level=str(item.level),
            # 写入时已脱敏；读取再脱敏一次，防历史行或人工写入绕过。
            message=sanitize_log_message(item.message),
            attempt=item.attempt,
            occurred_at=item.occurred_at,
        )


class ImportLogListOut(BaseModel):
    """步骤日志页；``truncated`` 与 ``next_cursor`` 表达同一事实。"""

    items: list[ImportLogOut]
    # 语义固定：true 当且仅当还有更早的日志未返回（即 next_cursor 非空）。
    # 不表示消息长度被截断——消息脱敏上限是另一条独立规则。
    truncated: bool
    # 下一页（更早日志）的游标；null 表示已经到最早一条。
    next_cursor: str | None


class MediaAssetOut(BaseModel):
    asset_id: UUID
    # 指向 Canonical 层 RawAsset。没有它，前端从「导入资产」无法跳到
    # 「原始资产」详情，调用方也只能靠 sha256 去猜是哪一个。
    raw_asset_id: UUID | None
    project_id: UUID
    data_source_id: UUID
    import_id: UUID
    episode_key: str
    path: str
    camera: str
    object_key: str
    size_bytes: int
    sha256: str
    content_type: str
    task_name: str
    excerpt_start_seconds: float | None
    excerpt_seconds: float | None
    source_fps: float | None
    created_at: datetime

    @classmethod
    def from_domain(cls, item: MediaAsset) -> MediaAssetOut:
        return cls(
            asset_id=item.asset_id,
            raw_asset_id=item.raw_asset_id,
            project_id=item.project_id,
            data_source_id=item.data_source_id,
            import_id=item.import_id,
            episode_key=item.episode_key,
            path=item.path,
            camera=item.camera,
            object_key=item.object_key,
            size_bytes=item.size_bytes,
            sha256=item.sha256,
            content_type=item.content_type,
            task_name=item.task_name,
            excerpt_start_seconds=item.excerpt_start_seconds,
            excerpt_seconds=item.excerpt_seconds,
            source_fps=item.source_fps,
            created_at=item.created_at,
        )


class MediaAssetListOut(BaseModel):
    """资产列表；契约里对应 `MediaAssetList`（无 next_cursor）。"""

    items: list[MediaAssetOut]


class MediaAssetPlaybackOut(BaseModel):
    """播放信息：预签名 URL 由服务端签发，前端不自行拼接存储路径。"""

    asset_id: UUID
    url: str
    expires_in_seconds: int
    content_type: str
    size_bytes: int


class ReviewEvidence(BaseModel):
    """审核依据：结构化的小对象，落库前由领域层再做一次白名单与字节数校验。"""

    model_config = ConfigDict(extra="forbid")

    timestamp_seconds: float | None = Field(default=None, ge=0)
    camera: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    episode_key: str | None = Field(default=None, max_length=128)


class ReviewCreateOut(BaseModel):
    review_id: UUID
    project_id: UUID
    import_id: UUID
    status: str
    decision: str | None
    reason: str | None
    reviewer_id: UUID | None
    submitted_by: UUID
    created_at: datetime
    decided_at: datetime | None
    version: int
    evidence: ReviewEvidence | None = None

    @classmethod
    def from_domain(cls, item: DataReviewT) -> ReviewCreateOut:
        evidence = ReviewEvidence(**dict(item.evidence)) if item.evidence is not None else None
        return cls(
            review_id=item.review_id,
            project_id=item.project_id,
            import_id=item.import_id,
            status=str(item.status),
            decision=str(item.decision) if item.decision else None,
            reason=item.reason,
            reviewer_id=item.reviewer_id,
            submitted_by=item.submitted_by,
            created_at=item.created_at,
            decided_at=item.decided_at,
            version=item.row_version,
            evidence=evidence,
        )


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(ACCEPT|REJECT|NEEDS_FIX)$")
    reason: str | None = Field(default=None, max_length=2000)
    evidence: ReviewEvidence | None = None


class QualityFindingOut(BaseModel):
    rule_id: str
    passed: bool
    detail: str


class QualityReportOut(BaseModel):
    import_id: UUID
    rule_version: str
    sample_scope: str
    total_assets: int
    total_episodes: int
    passed: bool
    findings: list[QualityFindingOut]
    failures: list[QualityFindingOut]


class ImportEpisodeMaterializeRequest(BaseModel):
    """可选场景标签；其余语义字段由导入产物推导，不接受客户端覆盖。"""

    model_config = ConfigDict(extra="forbid")

    scene_labels: list[str] = Field(default_factory=list, max_length=32)


class MaterializedEpisodeOut(BaseModel):
    episode_id: UUID
    episode_key: str
    raw_asset_ids: list[UUID]


class ImportEpisodeMaterializeResultOut(BaseModel):
    import_id: UUID
    episode_count: int
    episodes: list[MaterializedEpisodeOut]
    skipped_episode_keys: list[str]


class RemoteCheckOut(BaseModel):
    """服务端记录的最近一次远端校验结论（不含远端原始返回）。"""

    status: Literal["VERIFIED"]
    checked_at: datetime
    resolved_revision: str
    license: str | None
    is_gated: bool
    dataset_card_url: str | None


class DataSourceDetailOut(BaseModel):
    """数据源详情聚合：登记信息 + 导入任务 + 最近校验结论。"""

    data_source: DataSourceOut
    imports: list[DataSourceImportOut]
    remote_check: RemoteCheckOut | None
