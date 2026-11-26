"""LLM 训练数据导出用例（V52-037，契约 ``fragments/llm_export.yaml``）。

**单一权威**：SFT / DPO / PRM 三种格式的训练资格门禁（未审核 / 质检不合格 /
TIE 剔除）、字节级确定性、格式版本与 manifest 全部由
``data-platform/ddp_data/llm_export`` 实现。本模块只做两件事：

1. **最小构造映射**：HTTP/JSON 载荷 → 该库要求的领域记录
   （``SftConversationRecord.from_raw`` / ``PreferenceRecord.from_raw`` /
   ``TrajectoryRecord.from_raw``）；不复制任何门禁或格式规则。
2. **摘要**：把 ``ExportBundle`` 压成不含大文件内容的元数据
   （文件名 / sha256 / 字节数 / 行数 / 剔除统计）。

**只读计算**：不落库、不写对象存储、不回传训练文件内容；同一输入重复调用
结果一致（由库的确定性 writer 保证）。

**HTTP 护栏**：端点同步计算，单次最多 ``MAX_LLM_EXPORT_RECORDS`` 条，避免
一次请求长时间占用请求线程（库自身另有 20 万行上限，面向离线批处理）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ddp_data.llm_export import (
    FORMAT_VERSION,
    LlmExportError,
    PreferenceRecord,
    Provenance,
    QcStatus,
    ReviewStatus,
    SftConversationRecord,
)
from ddp_data.llm_export.prm import (
    PrmExportBundle,
    TrajectoryRecord,
    export_prm,
)
from ddp_data.llm_export.writers import (
    ExportBundle,
    LlmExportWriterError,
    export_dpo,
    export_sft,
)

from app.core.errors import ValidationFailedError

#: 对外可选的导出格式名（与库内 manifest.format 一一对应，不新增别名）。
SUPPORTED_EXPORT_FORMATS: tuple[str, ...] = ("SFT", "DPO", "PRM")

#: HTTP 同步计算的单次记录上限。库上限（200k 行）面向离线批处理，
#: 不适合放在请求线程里；超过本上限直接 422，不静默截断。
MAX_LLM_EXPORT_RECORDS = 5_000


def list_export_formats() -> list[dict[str, str]]:
    """三种训练格式及其格式版本（训练侧用于校验产物 manifest）。"""
    return [{"format": name, "format_version": FORMAT_VERSION} for name in SUPPORTED_EXPORT_FORMATS]


def format_llm_export(fmt: str, records: list[dict[str, object]]) -> dict[str, Any]:
    """导出为指定格式并返回摘要（不含文件内容）。

    记录必须已就绪（携带 provenance 与审核/质检结论）；不满足训练资格的记录
    由库的门禁剔除并计入 ``excluded_counts``，本节不做二次判定。
    """
    normalized = _normalize_format(fmt)
    if not records:
        raise ValidationFailedError("records 不能为空：至少需要 1 条记录")
    if len(records) > MAX_LLM_EXPORT_RECORDS:
        raise ValidationFailedError(
            f"records 超过单次上限 {MAX_LLM_EXPORT_RECORDS}；大批量导出请分批调用"
        )
    try:
        bundle = _export_bundle(normalized, records)
    except (LlmExportError, LlmExportWriterError) as exc:
        raise ValidationFailedError(str(exc)) from exc
    return _summarize(bundle)


# --------------------------------------------------------------------------- #
# 格式分发与记录构造（最小映射；复杂语义一律交给库）
# --------------------------------------------------------------------------- #
def _normalize_format(fmt: str) -> str:
    normalized = fmt.strip().upper()
    if normalized not in SUPPORTED_EXPORT_FORMATS:
        allowed = ", ".join(SUPPORTED_EXPORT_FORMATS)
        raise ValidationFailedError(f"不支持的导出格式 {fmt!r}：允许 {allowed}")
    return normalized


def _export_bundle(
    fmt: str,
    records: list[dict[str, object]],
) -> ExportBundle | PrmExportBundle:
    if fmt == "SFT":
        return export_sft(
            [_sft_record(_record_mapping(raw, index), index) for index, raw in enumerate(records)]
        )
    if fmt == "DPO":
        return export_dpo(
            [
                _preference_record(_record_mapping(raw, index), index)
                for index, raw in enumerate(records)
            ]
        )
    return export_prm(
        [
            _trajectory_record(_record_mapping(raw, index), index)
            for index, raw in enumerate(records)
        ]
    )


def _sft_record(record: Mapping[str, object], index: int) -> SftConversationRecord:
    where = f"records[{index}]"
    provenance = _provenance(record, index)
    messages = _mappings(record.get("messages"), f"{where}.messages")
    if not messages:
        raise ValidationFailedError(f"{where}.messages 不能为空")
    try:
        return SftConversationRecord.from_raw(provenance, messages)
    except LlmExportError as exc:
        raise ValidationFailedError(f"{where} 构造 SFT 记录失败：{exc}") from exc


def _preference_record(record: Mapping[str, object], index: int) -> PreferenceRecord:
    where = f"records[{index}]"
    provenance = _provenance(record, index)
    try:
        return PreferenceRecord.from_raw(
            provenance,
            prompt=_required_text(record, "prompt", where),
            response_a=_required_text(record, "response_a", where),
            response_b=_required_text(record, "response_b", where),
            winner=_required_text(record, "winner", where),
            rationale=_optional_text(record, "rationale", where),
            criteria_scores=_criteria_scores(record, where),
            confidence=_optional_float(record, "confidence", where),
            presented_order=_presented_order(record, where),
        )
    except LlmExportError as exc:
        raise ValidationFailedError(f"{where} 构造偏好记录失败：{exc}") from exc


def _trajectory_record(record: Mapping[str, object], index: int) -> TrajectoryRecord:
    where = f"records[{index}]"
    provenance = _provenance(record, index)
    steps = _mappings(record.get("steps"), f"{where}.steps")
    if not steps:
        raise ValidationFailedError(f"{where}.steps 不能为空")
    final_answer_correct = record.get("final_answer_correct")
    if final_answer_correct is not None and not isinstance(final_answer_correct, bool):
        raise ValidationFailedError(f"{where}.final_answer_correct 必须是布尔值或 null")
    try:
        return TrajectoryRecord.from_raw(
            provenance,
            trajectory_id=_required_text(record, "trajectory_id", where),
            task_goal=_required_text(record, "task_goal", where),
            steps=steps,
            final_answer_correct=final_answer_correct,
        )
    except LlmExportError as exc:
        raise ValidationFailedError(f"{where} 构造 PRM 轨迹失败：{exc}") from exc


def _provenance(record: Mapping[str, object], index: int) -> Provenance:
    where = f"records[{index}].provenance"
    raw = record.get("provenance")
    if not isinstance(raw, Mapping):
        raise ValidationFailedError(f"{where} 缺失或不是对象")
    return Provenance(
        dataset_version_id=_required_text(raw, "dataset_version_id", where),
        sample_id=_required_text(raw, "sample_id", where),
        submission_id=_required_text(raw, "submission_id", where),
        review_status=_enum_value(ReviewStatus, raw, "review_status", where),
        qc_status=_enum_value(QcStatus, raw, "qc_status", where),
        schema_version=_required_text(raw, "schema_version", where),
        guideline_version=_optional_text(raw, "guideline_version", where),
        judge_model=_optional_text(raw, "judge_model", where),
        annotator_refs=_text_tuple(raw.get("annotator_refs"), f"{where}.annotator_refs"),
    )


# --------------------------------------------------------------------------- #
# 原始值校验（错误统一 422；不吞异常）
# --------------------------------------------------------------------------- #
def _record_mapping(raw: object, index: int) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailedError(f"records[{index}] 必须是对象")
    return raw


def _required_text(container: Mapping[str, object], field: str, where: str) -> str:
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailedError(f"{where}.{field} 必须是非空字符串")
    return value


def _optional_text(container: Mapping[str, object], field: str, where: str) -> str | None:
    value = container.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailedError(f"{where}.{field} 必须是字符串或 null")
    return value


def _optional_float(container: Mapping[str, object], field: str, where: str) -> float | None:
    value = container.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationFailedError(f"{where}.{field} 必须是数值或 null")
    return float(value)


def _enum_value(
    enum_type: type[ReviewStatus] | type[QcStatus],
    container: Mapping[str, object],
    field: str,
    where: str,
) -> ReviewStatus | QcStatus:
    value = container.get(field)
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValidationFailedError(f"{where}.{field} 取值非法：{value!r}") from exc


def _text_tuple(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValidationFailedError(f"{where} 必须是字符串数组")
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationFailedError(f"{where} 元素必须是非空字符串")
        refs.append(item)
    return tuple(refs)


def _mappings(value: object, where: str) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationFailedError(f"{where} 必须是对象数组")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValidationFailedError(f"{where} 元素必须是对象")
        result.append(item)
    return result


def _criteria_scores(record: Mapping[str, object], where: str) -> dict[str, float] | None:
    value = record.get("criteria_scores")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValidationFailedError(f"{where}.criteria_scores 必须是对象或 null")
    scores: dict[str, float] = {}
    for key, score in value.items():
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ValidationFailedError(f"{where}.criteria_scores[{key!r}] 必须是数值")
        scores[str(key)] = float(score)
    return scores


def _presented_order(record: Mapping[str, object], where: str) -> tuple[str, str]:
    value = record.get("presented_order")
    if value is None:
        return ("A", "B")
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or any(slot not in ("A", "B") for slot in value)
    ):
        raise ValidationFailedError(f"{where}.presented_order 必须是 ['A','B'] 或 ['B','A']")
    return (str(value[0]), str(value[1]))


# --------------------------------------------------------------------------- #
# 摘要（不含文件内容；sha256 以实际字节复核，防止 manifest 与产物漂移）
# --------------------------------------------------------------------------- #
def _summarize(bundle: ExportBundle | PrmExportBundle) -> dict[str, Any]:
    manifest = bundle.manifest
    manifest_files = dict(manifest.get("files") or {})
    files = {
        name: {
            "sha256": _verified_digest(name, data, manifest_files),
            "bytes": len(data),
        }
        for name, data in bundle.files.items()
    }
    excluded_counts = dict(manifest.get("excluded_counts") or {})
    return {
        "format": str(manifest["format"]),
        "format_version": str(manifest["format_version"]),
        "generator_version": str(manifest["generator_version"]),
        "record_count": int(manifest["record_count"]),
        "excluded_counts": {str(key): int(count) for key, count in excluded_counts.items()},
        "files": dict(sorted(files.items())),
    }


def _verified_digest(name: str, data: bytes, manifest_files: Mapping[str, object]) -> str:
    """返回不含 ``sha256:`` 前缀的十六进制摘要；与 manifest 不一致视为实现缺陷。"""
    actual = "sha256:" + hashlib.sha256(data).hexdigest()
    expected = str(manifest_files.get(name, ""))
    if expected != actual:
        raise RuntimeError(f"导出产物 {name} 的 sha256 与 manifest 不一致")
    return actual.removeprefix("sha256:")


__all__ = [
    "MAX_LLM_EXPORT_RECORDS",
    "SUPPORTED_EXPORT_FORMATS",
    "format_llm_export",
    "list_export_formats",
]
