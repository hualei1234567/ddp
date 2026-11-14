"""数据源域请求/响应模型。

``extra="forbid"``：未知字段直接 422，避免客户端以为传了却静默忽略。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSource, DataSourceImport
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
    imported_row_count: int | None
    imported_file_count: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
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
            imported_row_count=item.imported_row_count,
            imported_file_count=item.imported_file_count,
            error_message=item.error_message,
            started_at=item.started_at,
            finished_at=item.finished_at,
            version=item.row_version,
            allowed_actions=sorted(allowed_import_actions(item.status)),
        )


class MediaAssetOut(BaseModel):
    asset_id: UUID
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


class MediaAssetPlaybackOut(BaseModel):
    """播放信息：预签名 URL 由服务端签发，前端不自行拼接存储路径。"""

    asset_id: UUID
    url: str
    expires_in_seconds: int
    content_type: str
    size_bytes: int
