"""处理阶段处理器（N4-002）。

从编排器中拆出，保持"编排"与"阶段能力"两块各自小于评审阈值：

- ``FILE_VALIDATION`` 有真实落点：只用对象存储的标量元数据（``object_size``）
  校验 ``input_refs`` 指向的对象存在，不把对象本体读进内存；
- 其余七个阶段在 N4-002 **没有真实实现**，一律由 :class:`SkippedStage`
  显式 SKIPPED 并写明原因——把未实现写成 ``SUCCEEDED`` 会让"处理完成"
  变成谎话，也会让下游误以为已有产物。

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
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.models import ProcessingRun


class StageFailure(Exception):
    """阶段的可预期失败；消息会脱敏后落库。"""

    def __init__(self, error_code: str, summary: str) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary


@dataclass(frozen=True, slots=True)
class StageContext:
    run: ProcessingRun
    scope: ProjectScope
    now: datetime


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: str
    log_summary: str
    output_refs: tuple[str, ...] = ()


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


class FileValidationHandler:
    """检查 ``input_refs`` 指向的对象在对象存储中确实存在。

    只读标量元数据（``object_size``），不读对象本体：大文件读取必须留在
    独立 Worker 的流式路径上，阶段编排不应把整个对象拉进内存。
    """

    def __init__(self, inspector: InputObjectInspector) -> None:
        self._inspector = inspector

    def __call__(self, context: StageContext) -> StageOutcome:
        total_bytes = 0
        for ref in context.run.input_refs:
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


#: 无真实实现阶段的跳过原因。逐阶段写明原因，方便前端与排障一眼看出
#: "为什么这条流水线没有产出"，而不是只看到一个笼统的 SKIPPED。
STAGE_SKIP_REASONS: Mapping[ProcessingStage, str] = {
    ProcessingStage.METADATA_EXTRACTION: (
        "无可复用的元数据解析产物：当前元数据解析发生在数据源导入链路，"
        "ProcessingRun 尚无独立解析产物落库语义"
    ),
    ProcessingStage.FORMAT_PARSING: (
        "无真实解析器接入：数据集格式解析由导入 Adapter 负责，本阶段尚未接线"
    ),
    ProcessingStage.TIME_ALIGNMENT: ("需要帧级多模态时间对齐能力（N3），当前流水线没有真实输入流"),
    ProcessingStage.CALIBRATION_VALIDATION: ("需要标定版本快照与真实传感器数据，本阶段尚未接入"),
    ProcessingStage.EPISODE_SEGMENTATION: (
        "切片入口在导入链路（POST /data-source-imports/{id}/episodes），本阶段尚未接线"
    ),
    ProcessingStage.DATA_CLEANING: "清洗规则尚未接入处理运行，本阶段没有真实实现",
    ProcessingStage.QUALITY_PRE_CHECK: (
        "现有质量规则 evaluate_import_quality 依赖导入导出的 MediaAsset/Episode 产物，"
        "ProcessingRun 尚无对应产物，不伪造检查结果"
    ),
}


def default_handlers(inspector: InputObjectInspector) -> dict[ProcessingStage, StageHandler]:
    """默认阶段表：FILE_VALIDATION 有真实落点，其余显式 SKIPPED。"""
    handlers: dict[ProcessingStage, StageHandler] = {
        ProcessingStage.FILE_VALIDATION: FileValidationHandler(inspector),
    }
    for stage, reason in STAGE_SKIP_REASONS.items():
        handlers[stage] = SkippedStage(reason)
    missing = [stage.value for stage in STAGE_SEQUENCE if stage not in handlers]
    if missing:  # 防御：新增阶段忘配处理器时启动即失败，而不是运行时静默成功。
        raise ValueError(f"以下阶段缺少处理器：{', '.join(missing)}")
    return handlers


__all__ = [
    "STAGE_SKIP_REASONS",
    "FileValidationHandler",
    "SkippedStage",
    "StageContext",
    "StageFailure",
    "StageHandler",
    "StageOutcome",
    "default_handlers",
]
