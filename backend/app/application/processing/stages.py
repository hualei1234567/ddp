"""处理阶段处理器（N4-002 / N4-005 / N4-002 增量）。

从编排器中拆出，保持"编排"与"阶段能力"两块各自小于评审阈值：

- ``FILE_VALIDATION`` 有真实落点：只用对象存储的标量元数据（``object_size``）
  校验 ``input_refs`` 指向的对象存在，不把对象本体读进内存；
- ``METADATA_EXTRACTION`` / ``FORMAT_PARSING`` 由
  :mod:`app.application.processing.metadata_stage` 实现：只读对象元数据
  （MP4/Parquet/MCAP）并投影为既有 Canonical 只读结构或阶段产物引用；
- ``QUALITY_PRE_CHECK`` 由 :mod:`app.application.processing.quality_stage` 实现，
  对本次运行关联的 Episode 逐个执行既有统一质量引擎；
- ``TIME_ALIGNMENT`` 由 :mod:`app.application.processing.time_alignment_stage`
  实现，只消费当前项目 canonical 命名空间下的严格版本化 manifest；
- ``CALIBRATION_VALIDATION`` 由
  :mod:`app.application.processing.calibration_stage` 实现，校验 Episode 冻结快照
  与项目内权威标定记录一致，不把当前状态变化误判为历史快照失效；
- ``EPISODE_SEGMENTATION`` 由
  :mod:`app.application.processing.segmentation_stage` 实现，对既有 Episode 的冻结
  规则版本做可追溯性检查，不在缺少规则参数时伪造重新切片；
- ``DATA_CLEANING`` 由 :mod:`app.application.processing.cleaning_stage` 实现，
  对 ``STREAM_EXTRACTION`` 产出的逐帧流执行确定性 FLAG 清洗与版本化报告落库；
  未注入清洗处理器时由 :class:`SkippedStage` 显式 SKIPPED 并写明原因。
- ``TIMESTAMP_NORMALIZE`` 由 :mod:`app.application.processing.timestamp_normalize_stage`
  实现，把 ``video_frames`` 的原始时间显式归一化并留痕；未注入处理器时由
  :class:`SkippedStage` 显式 SKIPPED 并写明原因。

阶段处理器是纯函数式对象：输入 :class:`StageContext`，返回
:class:`StageOutcome`；可预期失败抛 :class:`StageFailure`，由编排器脱敏落库。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.application.processing.ports import InputObjectInspector, ObjectInspectionError
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.models import ProcessingRun


class StageFailure(Exception):
    """阶段的可预期失败；消息会脱敏后落库。"""

    def __init__(self, error_code: str, summary: str, *, retryable: bool = True) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary
        # 永久性失败（输入引用越界、对象不存在）重试只会重复同一结果，
        # 让运行停在 RETRYING 并烧掉事件 attempts 是纯粹的噪声。
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class StageContext:
    run: ProcessingRun
    scope: ProjectScope
    now: datetime


@dataclass(frozen=True, slots=True)
class StageArtifact:
    """阶段产物的**描述**；落库由编排器在同一事务内完成（V31-016）。

    阶段是纯函数：它知道产物的类型、key、Schema 版本、内容类型、字节数与
    摘要，但**不**知道项目/运行/尝试/操作者——这些由编排器从上下文补齐。
    这样阶段无需依赖仓储，也不会因为"忘了传 project_id"而写出跨项目行。
    """

    kind: ProcessingArtifactKind
    object_key: str
    schema_version: str
    content_type: str
    size_bytes: int
    checksum_sha256: str | None = None
    #: 产物来源输入对象；无单一来源时为 ``None``。
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: str
    log_summary: str
    #: 兼容投影：既有调用方与契约仍读 ``output_refs``。
    output_refs: tuple[str, ...] = ()
    #: 产物描述：编排器据此登记 ``processing_artifacts``（V31-016）。
    artifacts: tuple[StageArtifact, ...] = ()


class StageHandler(Protocol):
    def __call__(self, context: StageContext) -> StageOutcome: ...


class SkippedStage:
    """没有真实实现的阶段：显式跳过并记录原因。"""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def __call__(self, context: StageContext) -> StageOutcome:
        return StageOutcome(
            status=ProcessingStepStatus.SKIPPED.value,
            log_summary=f"跳过：{self._reason}",
        )


#: 允许作为处理输入的顶层前缀；第二段必须是本项目的 project_id。
_PROJECT_SCOPED_ROOTS = frozenset({"raw", "canonical"})


def is_project_scoped(ref: str, project_id: str) -> bool:
    """``<root>/<project_id>/...`` 形式才允许被探测；其余一律拒绝。"""
    if not isinstance(ref, str) or not ref:
        return False
    parts = ref.split("/")
    if len(parts) < 3 or any(part in {"", ".", ".."} for part in parts):
        return False
    return parts[0] in _PROJECT_SCOPED_ROOTS and parts[1] == project_id


class FileValidationHandler:
    """检查 ``input_refs`` 指向的对象在对象存储中确实存在。

    只读标量元数据（``object_size``），不读对象本体：大文件读取必须留在
    独立 Worker 的流式路径上，阶段编排不应把整个对象拉进内存。
    """

    def __init__(self, inspector: InputObjectInspector) -> None:
        self._inspector = inspector

    def __call__(self, context: StageContext) -> StageOutcome:
        total_bytes = 0
        project_id = str(context.run.project_id)
        for ref in context.run.input_refs:
            # 输入引用只允许指向**当前项目**的对象。不做这一步，任何项目成员
            # 都能用一个猜到的 key 探测别的项目里对象是否存在、有多大
            # （`object_missing` 与"合计 N 字节"就是两个可观测侧信道），
            # 直接绕过服务端强制 project_id 隔离。
            if not is_project_scoped(ref, project_id):
                raise StageFailure(
                    "input_ref_not_project_scoped",
                    "输入引用必须属于当前项目",
                    retryable=False,
                )
            try:
                size = self._inspector.object_size(ref)
            except ObjectInspectionError as exc:
                raise StageFailure(
                    "object_storage_unavailable",
                    "对象存储不可用，无法校验输入",
                ) from exc
            if size is None:
                raise StageFailure("object_missing", f"输入对象不存在：{ref}")
            total_bytes += size
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"校验 {len(context.run.input_refs)} 个输入对象存在，合计 {total_bytes} 字节"
            ),
        )


#: 无真实实现阶段的跳过原因。逐阶段写明"缺什么、依赖哪个任务"，方便前端与
#: 排障一眼看出"为什么这条流水线没有产出"，而不是只看到一个笼统的 SKIPPED。
STAGE_SKIP_REASONS: Mapping[ProcessingStage, str] = {
    ProcessingStage.DATA_CLEANING: (
        "清洗规则尚未接入处理运行（缺少规则集版本与清洗产物落库语义），本阶段没有真实实现"
    ),
    # V6.1 视频链三阶段（V61-005/007）：枚举与迁移 0087 已就位，解码器接线
    # 在 T1-02（data-platform video_decode → 编排器注入）。未接线期间显式
    # SKIPPED——非视频样本（如 RoboMimic ph，无图像流）本就应跳过；
    # 注入后由 default_handlers 的 video_decode/video_frame_index/timestamp_normalize
    # 参数覆盖。
    ProcessingStage.VIDEO_DECODE: (
        "视频解码器未注入（T1-02 video_decode 接线中），非视频样本自然跳过"
    ),
    ProcessingStage.FRAME_INDEX: (
        "帧索引构建未注入（依赖 VIDEO_DECODE 产物），视频样本解码器接线后启用"
    ),
    ProcessingStage.TIMESTAMP_NORMALIZE: (
        "时间归一化未注入（依赖 FRAME_INDEX 的原始 pts），解码器接线后启用"
    ),
}


def default_handlers(
    inspector: InputObjectInspector,
    *,
    metadata_extraction: StageHandler,
    format_parsing: StageHandler,
    time_alignment: StageHandler,
    calibration_validation: StageHandler,
    episode_segmentation: StageHandler,
    quality_check: StageHandler,
    stream_extraction: StageHandler | None = None,
    data_cleaning: StageHandler | None = None,
    timestamp_normalize: StageHandler | None = None,
) -> dict[ProcessingStage, StageHandler]:
    """默认阶段表：必选阶段由组合根注入，可选阶段未注入时显式 SKIPPED。

    各真实处理器必须由组合根显式注入：静默回退到 SUCCEEDED 会让“阶段没接线”
    在生产里看起来像“没有需要处理的对象”。
    """
    handlers: dict[ProcessingStage, StageHandler] = {
        ProcessingStage.FILE_VALIDATION: FileValidationHandler(inspector),
        ProcessingStage.METADATA_EXTRACTION: metadata_extraction,
        ProcessingStage.FORMAT_PARSING: format_parsing,
        ProcessingStage.TIME_ALIGNMENT: time_alignment,
        ProcessingStage.CALIBRATION_VALIDATION: calibration_validation,
        ProcessingStage.EPISODE_SEGMENTATION: episode_segmentation,
        ProcessingStage.QUALITY_PRE_CHECK: quality_check,
    }
    # STREAM_EXTRACTION 需要对象存储读取 + 产物写入两项注入；未注入时显式
    # SKIPPED 并写明原因——静默成功会让「没接线」看起来像「没有需要提取的流」。
    if stream_extraction is not None:
        handlers[ProcessingStage.STREAM_EXTRACTION] = stream_extraction
    else:
        handlers[ProcessingStage.STREAM_EXTRACTION] = SkippedStage(
            "STREAM_EXTRACTION 未注入（需要对象存储读取与产物写入能力）"
        )
    for stage, reason in STAGE_SKIP_REASONS.items():
        handlers[stage] = SkippedStage(reason)
    if data_cleaning is not None:
        handlers[ProcessingStage.DATA_CLEANING] = data_cleaning
    # TIMESTAMP_NORMALIZE 需要数据库会话与 video_frames 读写语义；未注入时
    # 由 STAGE_SKIP_REASONS 的 SkippedStage 显式跳过并写明原因（同
    # STREAM_EXTRACTION 的可选注入模式），不静默成功。
    if timestamp_normalize is not None:
        handlers[ProcessingStage.TIMESTAMP_NORMALIZE] = timestamp_normalize
    missing = [stage.value for stage in STAGE_SEQUENCE if stage not in handlers]
    if missing:  # 防御：新增阶段忘配处理器时启动即失败，而不是运行时静默成功。
        raise ValueError(f"以下阶段缺少处理器：{', '.join(missing)}")
    return handlers


__all__ = [
    "STAGE_SKIP_REASONS",
    "FileValidationHandler",
    "SkippedStage",
    "StageArtifact",
    "StageContext",
    "StageFailure",
    "StageHandler",
    "StageOutcome",
    "default_handlers",
    "is_project_scoped",
]
