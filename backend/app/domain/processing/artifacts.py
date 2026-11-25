"""处理运行产物目录（V31-016）。

一个 :class:`ProcessingArtifact` 是"某次运行在某阶段产出了哪个对象"的
**不可变记录**。它把产物从 ``ProcessingRun.output_refs`` 的裸字符串数组
升级为一等实体：

- ``output_refs`` 只有 key，无法回答"这个产物是哪个阶段、哪次尝试、什么
  类型、多大、摘要是否一致"；
- 产物目录是权威记录，``output_refs`` 退化为兼容投影（既有调用方与契约
  不变）；
- 每条记录带 ``(run_id, stage, attempt, kind, object_key)`` 唯一键，重试
  会以新的 ``attempt`` 另起一行，历史产物可追溯。

**边界**

- 只存元数据与对象存储引用，**不存对象本体**；
- 领域层不依赖 ORM/队列；持久化由仓储把本对象映射为表行；
- 产物随运行级联删除（``run_id`` 外键 ``ON DELETE CASCADE``），不单独
  提供删除用例——运行是产物的生命周期边界。

**为什么不复用接入域的 ``CaptureSessionArtifact``**

后者是**只读视图**（不落库、不参与状态机），把 ``raw_assets`` /
``episodes`` 两张权威表投影成统一列表；处理产物有自己的阶段归属、尝试
序号与级联删除语义。强行合并会让"只读投影"与"可写实体"两种职责互相
污染，也会让接入域被迫认识处理域的阶段枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.processing.enums import ProcessingStage
from app.domain.processing.errors import DomainValidationError
from app.domain.processing.state_machines import coerce_stage
from app.domain.processing.validation import (
    non_negative_int,
    optional_aware,
    optional_text,
    positive_int,
    required_text,
    required_uuid,
)

#: 对象 key 上限：与 ``ProcessingRun.output_refs`` 的单项长度一致，避免
#: 同一 key 在两个实体里被不同长度截断。
ARTIFACT_OBJECT_KEY_MAX_LENGTH = 1024
#: 产物 Schema 版本上限；与 ``ProcessingRun.pipeline_version`` 同宽。
ARTIFACT_SCHEMA_VERSION_MAX_LENGTH = 64
#: 内容类型上限；MIME 类型远短于此，超长即视为脏数据。
ARTIFACT_CONTENT_TYPE_MAX_LENGTH = 128

_SHA256_HEX_LENGTH = 64
_SHA256_HEX_CHARS = frozenset("0123456789abcdef")


class ProcessingArtifactKind(StrEnum):
    """产物类型；取值覆盖各阶段真实产出的短名。

    只登记**对象存储中的产物**。``QUALITY_PRE_CHECK`` 的 ``quality-run/<id>``
    是数据库记录引用（质量结论已落 ``quality_runs`` 表），不是对象存储对象，
    因此不在此列——把它塞进来会让"产物"同时指两种东西。
    """

    METADATA = "METADATA"
    CANONICAL = "CANONICAL"
    TIME_ALIGNMENT_REPORT = "TIME_ALIGNMENT_REPORT"
    #: V51-001~003：从真实 payload 提取的 SensorStream / Observation / Action
    #: 物化产物（NDJSON）。与 METADATA 区分：METADATA 是"描述数据长什么样"，
    #: 本类型是"真的把逐帧值取出来了"，两者的下游消费者与失效条件都不同。
    STREAM_EXTRACTION = "STREAM_EXTRACTION"


def _coerce_kind(value: object) -> str:
    try:
        return ProcessingArtifactKind(value).value  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in ProcessingArtifactKind)
        raise DomainValidationError(f"kind 取值非法，允许值：{allowed}") from exc


def _checksum(value: object) -> str | None:
    """摘要要么是 64 位小写十六进制，要么为空。

    不接受"看起来像"的短串：摘要的全部价值在于可逐字节复算，长度或大小写
    不符即视为脏数据。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError("checksum_sha256 必须是字符串")
    if len(value) != _SHA256_HEX_LENGTH or not set(value) <= _SHA256_HEX_CHARS:
        raise DomainValidationError("checksum_sha256 必须是 64 位小写十六进制")
    return value


@dataclass(frozen=True, slots=True)
class ProcessingArtifact:
    """一条运行产物记录；不可变，重试以新的 ``attempt`` 另起一行。"""

    artifact_id: UUID
    project_id: UUID
    run_id: UUID
    stage: str
    attempt: int
    kind: str
    object_key: str
    schema_version: str
    content_type: str
    size_bytes: int
    checksum_sha256: str | None = None
    #: 产物来自哪个输入对象（例如 ``raw/<project>/<asset>``）；阶段产物无
    #: 单一来源时为 ``None``。
    source_ref: str | None = None
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", required_uuid(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "project_id", required_uuid(self.project_id, "project_id"))
        object.__setattr__(self, "run_id", required_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "stage", coerce_stage(self.stage))
        object.__setattr__(self, "attempt", positive_int(self.attempt, "attempt"))
        object.__setattr__(self, "kind", _coerce_kind(self.kind))
        object.__setattr__(
            self,
            "object_key",
            required_text(
                self.object_key,
                "object_key",
                max_length=ARTIFACT_OBJECT_KEY_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "schema_version",
            required_text(
                self.schema_version,
                "schema_version",
                max_length=ARTIFACT_SCHEMA_VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "content_type",
            required_text(
                self.content_type,
                "content_type",
                max_length=ARTIFACT_CONTENT_TYPE_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "size_bytes", non_negative_int(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "checksum_sha256", _checksum(self.checksum_sha256))
        object.__setattr__(
            self,
            "source_ref",
            optional_text(
                self.source_ref,
                "source_ref",
                max_length=ARTIFACT_OBJECT_KEY_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "created_at", optional_aware(self.created_at, "created_at"))
        if self.created_by is not None:
            object.__setattr__(
                self,
                "created_by",
                required_uuid(self.created_by, "created_by"),
            )

    @property
    def stage_enum(self) -> ProcessingStage:
        """阶段枚举视图；``stage`` 列已是合法值，这里只做类型收窄。"""
        return ProcessingStage(self.stage)


__all__ = [
    "ARTIFACT_CONTENT_TYPE_MAX_LENGTH",
    "ARTIFACT_OBJECT_KEY_MAX_LENGTH",
    "ARTIFACT_SCHEMA_VERSION_MAX_LENGTH",
    "ProcessingArtifact",
    "ProcessingArtifactKind",
]
