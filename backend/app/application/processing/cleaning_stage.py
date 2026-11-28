"""``DATA_CLEANING`` 确定性清洗阶段处理器（V51-006）。

职责与边界：

1. **有界读取上游流产物**：通过 ``ProcessingArtifactRepository`` 查询当前 ``run_id``
   已登记的 ``STREAM_EXTRACTION`` 产物，再用 ``StageArtifactStore.read_bytes``
   受控读取 NDJSON，不重读大体积原始 HDF5，也不爆内存。
2. **缺失即如实跳过**：当前运行没有 ``STREAM_EXTRACTION`` 产物（如仅输入 MP4
   或 Episode manifest）时，显式返回 ``ProcessingStepStatus.SKIPPED`` 并写明原因，
   绝不伪造成功或产出空壳报告。
3. **字节级幂等报告**：报告 JSON 不写入 ``datetime.now()`` 等动态时钟字段（执行
   时间由步骤表与 ``ProcessingArtifact.created_at`` 承载），保证同输入重跑字节级
   SHA256 完全一致；对象存储 key 固定为 ``processing-run/<project>/<run>/cleaning/report.json``。
4. **严守原始资产零篡改红线**：只新增衍生报告对象，绝不改写 ``RawAsset`` 或上游
   ``STREAM_EXTRACTION`` 产物字节（``raw_asset_mutated: False``）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from uuid import UUID

from app.application.processing.cleaning_transformed_writer import (
    cleaning_transformed_key,
    write_transformed_slices,
)
from app.application.processing.ports import (
    ObjectInspectionError,
    ObjectMissingError,
    ObjectTooLargeError,
    ProcessingArtifactRepository,
    StageArtifactStore,
)
from app.application.processing.stages import (
    StageArtifact,
    StageContext,
    StageFailure,
    StageOutcome,
)
from app.application.processing.stream_extraction_stage import STREAM_SCHEMA_VERSION
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.cleaning_actions import (
    CleaningActionResult,
    apply_cleaning_actions,
)
from app.domain.processing.cleaning_rules import (
    CLEANING_RULESET_VERSION,
    DEFAULT_CLEANING_RULESET,
    DEFAULT_CLEANING_THRESHOLDS,
    CleaningFrameSample,
    CleaningRuleSpec,
    CleaningStreamSlice,
    CleaningThresholds,
    evaluate_stream_records,
)
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.errors import DomainValidationError

#: 清洗报告产物自身 Schema 版本。
CLEANING_REPORT_SCHEMA_VERSION = "ddp.processing.cleaning-report/1.0.0"

#: 单个上游 STREAM_EXTRACTION NDJSON 产物读取上限（16 MiB）。
DEFAULT_MAX_STREAM_ARTIFACT_BYTES = 16 * 1024 * 1024

#: 单个清洗报告 JSON 产物大小上限（1 MiB）。
DEFAULT_MAX_CLEANING_REPORT_BYTES = 1024 * 1024

_ALLOWED_ACTION_SOURCES = frozenset({"PRESENT", "ACTION_SOURCE_ABSENT"})


def cleaning_report_key(project_id: UUID, run_id: UUID) -> str:
    """服务端生成的幂等清洗报告 Key（不含时间戳，同运行重跑覆盖同一路径）。"""
    return f"processing-run/{project_id}/{run_id}/cleaning/report.json"


class DataCleaningHandler:
    """消费同一运行的 ``STREAM_EXTRACTION`` 产物并生成确定性清洗报告。"""

    def __init__(
        self,
        artifact_catalog: ProcessingArtifactRepository,
        artifacts: StageArtifactStore,
        *,
        ruleset: Sequence[CleaningRuleSpec] = DEFAULT_CLEANING_RULESET,
        ruleset_version: str = CLEANING_RULESET_VERSION,
        thresholds: CleaningThresholds = DEFAULT_CLEANING_THRESHOLDS,
        disabled_rule_ids: AbstractSet[str] | Sequence[str] = (),
        quarantine_rule_ids: AbstractSet[str] | Sequence[str] = (),
        transform_rule_ids: AbstractSet[str] | Sequence[str] = (),
        max_stream_artifact_bytes: int = DEFAULT_MAX_STREAM_ARTIFACT_BYTES,
        max_report_bytes: int = DEFAULT_MAX_CLEANING_REPORT_BYTES,
    ) -> None:
        if max_stream_artifact_bytes <= 0 or max_report_bytes <= 0:
            raise ValueError("清洗阶段读取与报告字节上限必须为正整数")
        self._artifact_catalog = artifact_catalog
        self._artifacts = artifacts
        self._ruleset = tuple(ruleset)
        self._ruleset_version = ruleset_version
        self._thresholds = thresholds
        self._disabled_rule_ids = frozenset(disabled_rule_ids)
        self._quarantine_rule_ids = frozenset(quarantine_rule_ids)
        self._transform_rule_ids = frozenset(transform_rule_ids)
        self._max_stream_artifact_bytes = max_stream_artifact_bytes
        self._max_report_bytes = max_report_bytes

    def __call__(self, context: StageContext) -> StageOutcome:
        stream_artifacts = self._select_stream_artifacts(context)
        if not stream_artifacts:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=("跳过：当前运行未产出 STREAM_EXTRACTION 产物，无逐帧流数据可清洗"),
            )

        slices = [self._load_stream_slice(item) for item in stream_artifacts]
        try:
            evaluation = evaluate_stream_records(
                slices,
                ruleset=self._ruleset,
                ruleset_version=self._ruleset_version,
                thresholds=self._thresholds,
                disabled_rule_ids=self._disabled_rule_ids,
            )
            action_result = apply_cleaning_actions(
                slices,
                evaluation,
                thresholds=self._thresholds,
                quarantine_rule_ids=self._quarantine_rule_ids,
                transform_rule_ids=self._transform_rule_ids,
            )
        except DomainValidationError as exc:
            raise StageFailure("cleaning_config_invalid", str(exc), retryable=False) from exc

        transformed_artifacts = write_transformed_slices(
            self._artifacts,
            project_id=context.run.project_id,
            run_id=context.run.run_id,
            transformed_slices=action_result.transformed_slices,
        )
        report_ref = cleaning_report_key(context.run.project_id, context.run.run_id)
        document = self._build_report_document(
            context.run.run_id,
            stream_artifacts,
            evaluation,
            action_result=action_result,
            transformed_refs=[item.object_key for item in transformed_artifacts],
        )
        report_artifact = self._write_report(report_ref, document, stream_artifacts)
        all_artifacts = (report_artifact, *transformed_artifacts)
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"确定性清洗完成（规则集 {evaluation.ruleset_version}）："
                f"评估 {len(slices)} 个流产物，启用规则 {len(evaluation.evaluated_rule_ids)} 条，"
                f"命中 {len(evaluation.hits)} 条，隔离 {len(action_result.quarantined)} 个，"
                f"衍生变换 {len(transformed_artifacts)} 个，原始资产零改动"
            ),
            output_refs=tuple(item.object_key for item in all_artifacts),
            artifacts=all_artifacts,
        )

    def _select_stream_artifacts(self, context: StageContext) -> list[ProcessingArtifact]:
        """按 ``object_key`` 选取最新 ``attempt`` 的 ``STREAM_EXTRACTION`` 产物并稳定排序。"""
        registered = self._artifact_catalog.list(
            context.scope,
            run_id=context.run.run_id,
            kind=ProcessingArtifactKind.STREAM_EXTRACTION.value,
        )
        latest_by_key: dict[str, ProcessingArtifact] = {}
        for item in registered:
            prev = latest_by_key.get(item.object_key)
            if prev is None or item.attempt >= prev.attempt:
                latest_by_key[item.object_key] = item
        return [latest_by_key[key] for key in sorted(latest_by_key)]

    def _load_stream_slice(self, artifact: ProcessingArtifact) -> CleaningStreamSlice:
        raw = self._read_bounded(artifact.object_key)
        lines = [line for line in raw.splitlines() if line.strip()]
        if not lines:
            raise StageFailure(
                "cleaning_input_invalid",
                "流提取产物为空，缺少 _meta 首行",
                retryable=False,
            )
        action_source = _parse_meta_line(lines[0])
        observations: list[CleaningFrameSample] = []
        actions: list[CleaningFrameSample] = []
        for line_no, raw_line in enumerate(lines[1:], start=2):
            kind, sample = _parse_frame_line(raw_line, line_no=line_no)
            if kind == "observation":
                observations.append(sample)
            else:
                actions.append(sample)
        return CleaningStreamSlice(
            target_ref=artifact.object_key,
            source_ref=artifact.source_ref,
            action_source=action_source,
            observations=tuple(observations),
            actions=tuple(actions),
        )

    def _read_bounded(self, object_key: str) -> bytes:
        try:
            return self._artifacts.read_bytes(
                object_key,
                max_bytes=self._max_stream_artifact_bytes,
            )
        except ObjectMissingError as exc:
            raise StageFailure(
                "cleaning_input_missing",
                "上游流提取产物不存在，可重试",
                retryable=True,
            ) from exc
        except ObjectTooLargeError as exc:
            raise StageFailure(
                "cleaning_input_too_large",
                f"上游流提取产物超过 {self._max_stream_artifact_bytes} 字节读取上限",
                retryable=False,
            ) from exc
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法读取流提取产物",
                retryable=True,
            ) from exc

    @staticmethod
    def _build_report_document(
        run_id: UUID,
        stream_artifacts: Sequence[ProcessingArtifact],
        evaluation,
        *,
        action_result: CleaningActionResult,
        transformed_refs: Sequence[str],
    ) -> dict[str, object]:
        payload = evaluation.to_payload()
        # 若衍生动作实际修改了衍生数据，如实上浮标志（原始资产始终 False）。
        payload["derived_data_mutated"] = bool(
            payload.get("derived_data_mutated") or action_result.derived_data_mutated
        )
        return {
            "schema_version": CLEANING_REPORT_SCHEMA_VERSION,
            "run_id": str(run_id),
            "input_artifacts": [
                {
                    "object_key": item.object_key,
                    "checksum_sha256": item.checksum_sha256,
                    "source_ref": item.source_ref,
                }
                for item in stream_artifacts
            ],
            "evaluation_sha256": evaluation.deterministic_sha256(),
            "quarantined_targets": [item.to_dict() for item in action_result.quarantined],
            "transformed_artifacts": [
                {
                    "object_key": ref,
                    "source_target_ref": slice_item.target_ref,
                    "source_ref": slice_item.source_ref,
                    "provenance": slice_item.transform_provenance,
                }
                for ref, slice_item in zip(
                    transformed_refs, action_result.transformed_slices, strict=True
                )
            ],
            **payload,
        }

    def _write_report(
        self,
        report_ref: str,
        document: dict[str, object],
        stream_artifacts: Sequence[ProcessingArtifact],
    ) -> StageArtifact:
        data = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(data) > self._max_report_bytes:
            raise StageFailure(
                "cleaning_report_too_large",
                f"清洗报告 {len(data)} 字节超过上限 {self._max_report_bytes}",
                retryable=False,
            )
        try:
            self._artifacts.put_bytes(report_ref, data)
        except ObjectInspectionError as exc:
            raise StageFailure(
                "object_storage_unavailable",
                "对象存储不可用，无法写入清洗报告",
                retryable=True,
            ) from exc
        sources = {item.source_ref for item in stream_artifacts if item.source_ref is not None}
        source_ref = next(iter(sources)) if len(sources) == 1 else None
        # 复用现有 ProcessingArtifactKind.CANONICAL 登记衍生清洗报告，与
        # FormatParsingHandler 的阶段衍生 JSON 语义一致，不引入额外迁移约束冲突。
        return StageArtifact(
            kind=ProcessingArtifactKind.CANONICAL,
            object_key=report_ref,
            schema_version=CLEANING_REPORT_SCHEMA_VERSION,
            content_type="application/json",
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            source_ref=source_ref,
        )


def _parse_meta_line(raw_line: bytes) -> str:
    payload = _decode_json_object(raw_line, line_no=1)
    meta = payload.get("_meta")
    if not isinstance(meta, Mapping):
        raise StageFailure(
            "cleaning_input_invalid", "流提取产物首行缺少 _meta 对象", retryable=False
        )
    if meta.get("schema_version") != STREAM_SCHEMA_VERSION:
        raise StageFailure(
            "cleaning_input_invalid",
            "流提取产物 schema_version 不受支持",
            retryable=False,
        )
    action_source = meta.get("action_source")
    if not isinstance(action_source, str) or action_source not in _ALLOWED_ACTION_SOURCES:
        raise StageFailure(
            "cleaning_input_invalid",
            "流提取产物 _meta.action_source 非法",
            retryable=False,
        )
    return action_source


def _parse_frame_line(raw_line: bytes, *, line_no: int) -> tuple[str, CleaningFrameSample]:
    payload = _decode_json_object(raw_line, line_no=line_no)
    record_type = payload.get("type")
    if record_type == "observation":
        id_field = "observation_id"
    elif record_type == "action":
        id_field = "action_id"
    else:
        raise StageFailure(
            "cleaning_input_invalid",
            f"流提取产物第 {line_no} 行包含未知记录类型",
            retryable=False,
        )
    raw_values = payload.get("values")
    if not isinstance(raw_values, list):
        raise StageFailure(
            "cleaning_input_invalid",
            f"流提取产物第 {line_no} 行 values 必须是数组",
            retryable=False,
        )
    raw_id = payload.get(id_field)
    frame_index = payload.get("frame_index")
    timestamp_ns = payload.get("timestamp_ns")
    try:
        sample = CleaningFrameSample(
            record_id=raw_id if isinstance(raw_id, str) else "",
            frame_index=frame_index,  # type: ignore[arg-type]
            values=tuple(raw_values),
            timestamp_ns=timestamp_ns,  # type: ignore[arg-type]
        )
    except DomainValidationError as exc:
        raise StageFailure(
            "cleaning_input_invalid",
            f"流提取产物第 {line_no} 行字段非法：{exc}",
            retryable=False,
        ) from exc
    return record_type, sample


def _decode_json_object(raw_line: bytes, *, line_no: int) -> Mapping[str, object]:
    try:
        parsed = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageFailure(
            "cleaning_input_invalid",
            f"流提取产物第 {line_no} 行不是合法 JSON",
            retryable=False,
        ) from exc
    if not isinstance(parsed, Mapping):
        raise StageFailure(
            "cleaning_input_invalid",
            f"流提取产物第 {line_no} 行必须是 JSON 对象",
            retryable=False,
        )
    return parsed


__all__ = [
    "CLEANING_REPORT_SCHEMA_VERSION",
    "DEFAULT_MAX_CLEANING_REPORT_BYTES",
    "DEFAULT_MAX_STREAM_ARTIFACT_BYTES",
    "DataCleaningHandler",
    "cleaning_report_key",
    "cleaning_transformed_key",
]
