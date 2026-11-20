"""严格 manifest 驱动的 ``TIME_ALIGNMENT`` 处理阶段。"""

from __future__ import annotations

import json
from uuid import UUID

from app.application.processing.ports import (
    InputObjectReader,
    ObjectInspectionError,
    ObjectMissingError,
    StageArtifactStore,
)
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.episodes.sync_analysis import analyze_stream_sync
from app.domain.episodes.sync_drift import SyncDriftError
from app.domain.episodes.time_alignment_manifest import parse_time_alignment_manifest
from app.domain.processing.enums import ProcessingStepStatus

TIME_ALIGNMENT_REPORT_SCHEMA_VERSION = "ddp.processing.time-alignment-report/1.0.0"
DEFAULT_MAX_TIME_ALIGNMENT_ARTIFACT_BYTES = 256 * 1024


def time_alignment_report_key(project_id: UUID, run_id: UUID) -> str:
    """返回服务端生成、可幂等覆盖的分析报告 Key。"""
    return f"processing-run/{project_id}/{run_id}/time-alignment/analysis.json"


def is_time_alignment_manifest_ref(ref: str, project_id: UUID) -> bool:
    """仅接受当前项目 canonical time-alignment 命名空间。"""
    if not isinstance(ref, str) or not ref:
        return False
    parts = ref.split("/")
    return (
        len(parts) >= 4
        and not any(part in {"", ".", ".."} for part in parts)
        and parts[0] == "canonical"
        and parts[1] == str(project_id)
        and parts[2] == "time-alignment"
    )


class TimeAlignmentHandler:
    """读取唯一的版本化 manifest，逐 group 复用领域同步分析。"""

    def __init__(
        self,
        reader: InputObjectReader,
        artifacts: StageArtifactStore,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_TIME_ALIGNMENT_ARTIFACT_BYTES,
    ) -> None:
        if max_artifact_bytes <= 0:
            raise ValueError("时间对齐产物读取上限必须为正整数")
        self._reader = reader
        self._artifacts = artifacts
        self._max_artifact_bytes = max_artifact_bytes

    def __call__(self, context: StageContext) -> StageOutcome:
        refs = tuple(
            ref
            for ref in context.run.input_refs
            if is_time_alignment_manifest_ref(ref, context.run.project_id)
        )
        if not refs:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：未提供版本化 time-alignment manifest",
            )
        if len(refs) > 1:
            raise StageFailure(
                "time_alignment_manifest_ambiguous",
                "同一处理运行只能提供一个 time-alignment manifest",
                retryable=False,
            )

        source_ref = refs[0]
        manifest = self._load_manifest(source_ref)
        if not manifest.groups:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：time-alignment manifest 的 groups 为空",
            )

        groups = [
            {
                "group_key": group.group_key,
                "analysis": analyze_stream_sync(group.windows, group.profiles).to_payload(),
            }
            for group in manifest.groups
        ]
        document: dict[str, object] = {
            "schema_version": TIME_ALIGNMENT_REPORT_SCHEMA_VERSION,
            "source_manifest_ref": source_ref,
            "source_manifest_schema_version": manifest.schema_version,
            "time_unit": manifest.time_unit,
            "groups": groups,
        }
        output_ref = time_alignment_report_key(
            context.run.project_id,
            context.run.run_id,
        )
        self._write_report(output_ref, document)
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=f"分析 {len(groups)} 个时间对齐分组，产出 1 个只读报告",
            output_refs=(output_ref,),
        )

    def _load_manifest(self, ref: str):
        try:
            size = self._reader.object_size(ref)
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法读取时间对齐 manifest",
            ) from exc
        if size is None:
            raise StageFailure(
                "time_alignment_artifact_missing",
                "时间对齐 manifest 不存在",
                retryable=False,
            )
        if size > self._max_artifact_bytes:
            raise StageFailure(
                "time_alignment_artifact_too_large",
                f"时间对齐 manifest 超过 {self._max_artifact_bytes} 字节上限",
                retryable=False,
            )
        try:
            data = self._reader.read_range(ref, start=0, length=size)
        except ObjectMissingError as exc:
            raise StageFailure(
                "time_alignment_artifact_missing",
                "时间对齐 manifest 不存在",
                retryable=False,
            ) from exc
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法读取时间对齐 manifest",
            ) from exc
        if len(data) != size:
            raise StageFailure(
                "object_storage_unavailable",
                "时间对齐 manifest 读取不完整",
            )
        try:
            document = json.loads(data)
            return parse_time_alignment_manifest(document)
        except (json.JSONDecodeError, UnicodeDecodeError, SyncDriftError) as exc:
            raise StageFailure(
                "time_alignment_artifact_invalid",
                "时间对齐 manifest 不符合版本化契约",
                retryable=False,
            ) from exc

    def _write_report(self, ref: str, document: dict[str, object]) -> None:
        data = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) > self._max_artifact_bytes:
            raise StageFailure(
                "time_alignment_report_too_large",
                f"时间对齐报告超过 {self._max_artifact_bytes} 字节上限",
                retryable=False,
            )
        try:
            self._artifacts.put_bytes(ref, data)
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法写入时间对齐报告",
            ) from exc


__all__ = [
    "DEFAULT_MAX_TIME_ALIGNMENT_ARTIFACT_BYTES",
    "TIME_ALIGNMENT_REPORT_SCHEMA_VERSION",
    "TimeAlignmentHandler",
    "is_time_alignment_manifest_ref",
    "time_alignment_report_key",
]
