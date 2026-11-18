"""数据源域聚合根与实体。

``DataSource`` 记录"登记了哪个外部数据集"，``DataSourceImport`` 记录
"用哪个工具、在什么范围、导入了多少、结果如何"（`docs/ddp/07` §2 要求的字段）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.datasources.enums import (
    AccessCondition,
    DataSourceKind,
    ImportLogLevel,
    ImportScope,
    ImportStage,
    ImportStatus,
)
from app.domain.datasources.errors import DomainValidationError

CODE_MAX_LENGTH = 64
NAME_MAX_LENGTH = 200
REPO_ID_MAX_LENGTH = 200
REVISION_MAX_LENGTH = 100
LICENSE_MAX_LENGTH = 100
URL_MAX_LENGTH = 2048
NOTE_MAX_LENGTH = 2000
IMPORT_LOG_MESSAGE_MAX_LENGTH = 1000


def _normalized(value: str, field_name: str, *, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 长度不能超过 {max_length}")
    return text


def _optional(value: str | None, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 长度不能超过 {max_length}")
    return text


@dataclass(frozen=True, slots=True)
class DataSource:
    """一个已登记的外部数据集来源。

    ``revision`` 必填：HF 上的 ``main`` 会移动，只有 commit sha 能复现导入。
    """

    data_source_id: UUID
    project_id: UUID
    code: str
    name: str
    kind: DataSourceKind | str
    repo_id: str
    revision: str
    license: str
    access_condition: AccessCondition | str
    is_gated: bool = False
    config_name: str | None = None
    split: str | None = None
    dataset_card_url: str | None = None
    notes: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    row_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _normalized(self.code, "code", max_length=CODE_MAX_LENGTH))
        object.__setattr__(self, "name", _normalized(self.name, "name", max_length=NAME_MAX_LENGTH))
        object.__setattr__(
            self, "repo_id", _normalized(self.repo_id, "repo_id", max_length=REPO_ID_MAX_LENGTH)
        )
        object.__setattr__(
            self,
            "revision",
            _normalized(self.revision, "revision", max_length=REVISION_MAX_LENGTH),
        )
        object.__setattr__(
            self, "license", _normalized(self.license, "license", max_length=LICENSE_MAX_LENGTH)
        )
        object.__setattr__(
            self,
            "kind",
            DataSourceKind(self.kind) if not isinstance(self.kind, DataSourceKind) else self.kind,
        )
        object.__setattr__(
            self,
            "access_condition",
            (
                AccessCondition(self.access_condition)
                if not isinstance(self.access_condition, AccessCondition)
                else self.access_condition
            ),
        )
        for name, limit in (
            ("config_name", REVISION_MAX_LENGTH),
            ("split", REVISION_MAX_LENGTH),
            ("dataset_card_url", URL_MAX_LENGTH),
            ("notes", NOTE_MAX_LENGTH),
        ):
            object.__setattr__(self, name, _optional(getattr(self, name), name, max_length=limit))
        # GATED 与 is_gated 必须一致，避免"标了公开实际要申请"。
        if self.access_condition is AccessCondition.GATED and not self.is_gated:
            raise DomainValidationError("access_condition=GATED 时 is_gated 必须为 true")
        if self.is_gated and self.access_condition is AccessCondition.PUBLIC:
            raise DomainValidationError("is_gated=true 时 access_condition 不能为 PUBLIC")
        if self.row_version < 1:
            raise DomainValidationError("row_version 必须大于等于 1")


@dataclass(frozen=True, slots=True)
class DataSourceImport:
    """一次导入尝试；失败也要留记录（`docs/ddp/07` §2、§5）。"""

    import_id: UUID
    project_id: UUID
    data_source_id: UUID
    status: ImportStatus | str
    scope: ImportScope | str
    tool_name: str
    tool_version: str
    sample_limit: int | None = None
    cache_location: str | None = None
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    imported_row_count: int | None = None
    imported_file_count: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # 运行期可观测性（迁移 0055）。NULL 表示"未测量"：迁移前的存量导入没有
    # 阶段与进度信息，绝不回填 0 冒充"已完成"。
    #
    # ``processed_items`` / ``total_items`` 的单位是**媒体文件**（导入产出的
    # 资产数），``total_items`` 在元数据解析完成后才可知。二者都为非空且
    # ``total_items > 0`` 时前端才展示百分比；否则显示"未测量"。
    stage: ImportStage | str | None = None
    processed_items: int | None = None
    total_items: int | None = None
    # 已执行的尝试次数（从 1 开始）。用于「可重试失败」与「彻底失败」的区分：
    # 导入任务进 FAILED 后按契约不得再动，所以第一次失败必须能退回 PENDING
    # 重新领取；attempt 与 max_attempts 决定还能退几次。
    attempt: int = 1
    max_attempts: int = 3
    row_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            ImportStatus(self.status) if not isinstance(self.status, ImportStatus) else self.status,
        )
        object.__setattr__(
            self,
            "scope",
            ImportScope(self.scope) if not isinstance(self.scope, ImportScope) else self.scope,
        )
        object.__setattr__(
            self, "tool_name", _normalized(self.tool_name, "tool_name", max_length=NAME_MAX_LENGTH)
        )
        object.__setattr__(
            self,
            "tool_version",
            _normalized(self.tool_version, "tool_version", max_length=REVISION_MAX_LENGTH),
        )
        if self.sample_limit is not None and self.sample_limit < 1:
            raise DomainValidationError("sample_limit 必须大于 0")
        if self.scope is ImportScope.SAMPLE and self.sample_limit is None:
            raise DomainValidationError("scope=SAMPLE 时必须给出 sample_limit")
        if self.row_version < 1:
            raise DomainValidationError("row_version 必须大于等于 1")
        if self.attempt < 1:
            raise DomainValidationError("attempt 必须大于等于 1")
        if self.max_attempts < 1:
            raise DomainValidationError("max_attempts 必须大于等于 1")
        if self.attempt > self.max_attempts:
            raise DomainValidationError("attempt 不得超过 max_attempts")
        if self.stage is not None:
            object.__setattr__(self, "stage", ImportStage(self.stage))
        for name in ("processed_items", "total_items"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise DomainValidationError(f"{name} 不能为负数")
        if (
            self.processed_items is not None
            and self.total_items is not None
            and self.processed_items > self.total_items
        ):
            raise DomainValidationError("processed_items 不得超过 total_items")

    @property
    def can_retry(self) -> bool:
        """是否还能重试：失败且未达上限。

        与 ``IngestionJob.can_retry`` 同一语义——两处规则一致，不存在
        「导入能重试、接入任务不能」这类分叉。
        """
        return self.status is ImportStatus.FAILED and self.attempt < self.max_attempts


@dataclass(frozen=True, slots=True)
class DataSourceImportLog:
    """导入执行期的一条结构化步骤日志（只追加、只读）。

    为什么单独建表而不复用 ``audit_logs``：审计记录的是"谁在什么时候把什么
    改成了什么"，导入阶段进度是高频运行期遥测，写进审计会污染变更时间线，
    也无法表达 ``stage`` / ``level``。本实体只承载排障信息，不参与业务状态。

    ``message`` 在应用层写入前已脱敏（URL / 预签名参数 / 绝对路径），
    这里只做非空与长度校验。
    """

    log_id: UUID
    project_id: UUID
    import_id: UUID
    stage: ImportStage | str
    level: ImportLogLevel | str
    message: str
    occurred_at: datetime
    # 同一导入内的单调序号（取写日志时的导入行版本号）。时间戳在注入时钟/
    # 快速连续写入时可能相同，没有它日志顺序就只能靠随机 UUID。
    sequence_no: int
    attempt: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", ImportStage(self.stage))
        object.__setattr__(self, "level", ImportLogLevel(self.level))
        text = (self.message or "").strip()
        if not text:
            raise DomainValidationError("日志 message 不能为空")
        if len(text) > IMPORT_LOG_MESSAGE_MAX_LENGTH:
            raise DomainValidationError(
                f"日志 message 长度不能超过 {IMPORT_LOG_MESSAGE_MAX_LENGTH}"
            )
        if self.attempt < 1:
            raise DomainValidationError("attempt 必须大于等于 1")
        if self.sequence_no < 1:
            raise DomainValidationError("sequence_no 必须大于等于 1")
        object.__setattr__(self, "message", text)
