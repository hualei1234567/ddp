"""SFT_MESSAGES_JSONL 与 PREFERENCE_DPO_JSONL 的确定性 writer。

字节级确定性：相同输入 → 相同字节（含 ``json.dumps(..., sort_keys=True)``）。
Provenance 默认写 sidecar（``provenance.jsonl``，按行号对齐），训练文件保持纯净。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import (
    DPO_FORMAT,
    GENERATOR_VERSION,
    SFT_FORMAT,
    FORMAT_VERSION,
    ChatMessage,
    EligibilityReport,
    PreferenceRecord,
    Provenance,
    SftConversationRecord,
    ExclusionReason,
    partition_by_eligibility,
)

_MAX_EXPORT_ROWS = 200_000


class LlmExportWriterError(ValueError):
    """writer 层错误（混批 / 超限）。"""


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """一次导出的全部文件（相对路径 -> 字节），外加 manifest。"""

    files: dict[str, bytes]
    manifest: dict[str, Any]


def _dump_line(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _message_dicts(messages: tuple[ChatMessage, ...]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _sft_payload(
    record: SftConversationRecord, include_metadata: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {"messages": _message_dicts(record.messages)}
    if include_metadata:
        payload["metadata"] = {"sample_id": record.provenance.sample_id}
    return payload


def _dpo_payload(record: PreferenceRecord, conversational: bool) -> dict[str, Any]:
    def _side(value: str | tuple[ChatMessage, ...]) -> Any:
        if conversational:
            return _message_dicts(value if isinstance(value, tuple) else ())
        return value

    def _side_or_str(value: str | tuple[ChatMessage, ...]) -> Any:
        if isinstance(value, tuple):
            return _message_dicts(value)
        return value

    prompt: Any = _side_or_str(record.prompt)
    chosen: Any = _side_or_str(record.chosen)
    rejected: Any = _side_or_str(record.rejected)
    if conversational:
        if isinstance(record.prompt, str):
            prompt = [{"role": "user", "content": record.prompt}]
        if isinstance(record.chosen, str):
            chosen = [{"role": "assistant", "content": record.chosen}]
        if isinstance(record.rejected, str):
            rejected = [{"role": "assistant", "content": record.rejected}]
        else:
            chosen = _side(record.chosen)
            rejected = _side(record.rejected)
        if isinstance(record.prompt, tuple):
            prompt = _message_dicts(record.prompt)
    return {"prompt": prompt, "chosen": chosen, "rejected": rejected}


def _provenance_line(index: int, prov: Provenance, extra: dict[str, Any]) -> bytes:
    doc: dict[str, Any] = {
        "row": index,
        "dataset_version_id": prov.dataset_version_id,
        "sample_id": prov.sample_id,
        "submission_id": prov.submission_id,
        "review_status": prov.review_status.value,
        "qc_status": prov.qc_status.value,
        "schema_version": prov.schema_version,
        "guideline_version": prov.guideline_version,
        "judge_model": prov.judge_model,
        "annotator_refs": list(prov.annotator_refs),
    }
    doc.update(extra)
    return _dump_line(doc)


def _build_manifest(
    fmt: str,
    files: dict[str, bytes],
    total: int,
    excluded: EligibilityReport,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "format": fmt,
        "format_version": FORMAT_VERSION,
        "generator_version": GENERATOR_VERSION,
        "record_count": total,
        "excluded_counts": dict(sorted(excluded.excluded_counts.items())),
        "excluded_total": len(excluded.excluded),
        "files": {
            name: "sha256:" + hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())
        },
    }
    if extra:
        manifest.update(extra)
    return manifest


def export_sft(
    records: Sequence[SftConversationRecord],
    *,
    provenance_in_metadata: bool = False,
) -> ExportBundle:
    """导出 SFT_MESSAGES_JSONL（OpenAI 兼容 ``{"messages":[...]}``）。

    ``provenance_in_metadata=False``（默认）：Provenance 写 sidecar，训练文件
    只含 ``messages``——喂给训练框架前不需要再剥离自定义键。
    """
    if not records:
        raise LlmExportWriterError("SFT 导出至少需要 1 条记录")
    if len(records) > _MAX_EXPORT_ROWS:
        raise LlmExportWriterError(f"超过单次导出上限 {_MAX_EXPORT_ROWS}")
    report = partition_by_eligibility(records)
    eligible_ids = {id(p) for p in report.eligible}
    kept = [r for r in records if id(r.provenance) in eligible_ids]

    lines: list[bytes] = []
    prov_lines: list[bytes] = []
    for idx, record in enumerate(kept):
        lines.append(_dump_line(_sft_payload(record, provenance_in_metadata)))
        prov_lines.append(_provenance_line(idx, record.provenance, {}))

    files = {
        "sft_messages.jsonl": b"".join(lines),
        "provenance.jsonl": b"".join(prov_lines),
    }
    manifest = _build_manifest(SFT_FORMAT, files, len(kept), report)
    return ExportBundle(files=files, manifest=manifest)


def export_dpo(
    records: Sequence[PreferenceRecord],
    *,
    conversational: bool = False,
    include_rationale_sidecar: bool = True,
) -> ExportBundle:
    """导出 PREFERENCE_DPO_JSONL（TRL 兼容 ``{"prompt","chosen","rejected"}``）。

    - canonical 语义：winner 已在 :class:`PreferenceRecord` 构造期归一，
      ``presented_order`` 只进 sidecar 供复盘；
    - ``conversational=True`` 输出消息数组形态（TRL ``is_conversational`` 分支）；
    - ``rationale`` / ``criteria_scores`` / ``confidence`` 只进 sidecar。
    """
    if not records:
        raise LlmExportWriterError("DPO 导出至少需要 1 条记录")
    if len(records) > _MAX_EXPORT_ROWS:
        raise LlmExportWriterError(f"超过单次导出上限 {_MAX_EXPORT_ROWS}")
    report = partition_by_eligibility(records)  # TIE 在门禁层按 PREFERENCE_TIE 剔除
    eligible_ids = {id(p) for p in report.eligible}
    kept = [r for r in records if id(r.provenance) in eligible_ids]
    if not kept:
        raise LlmExportWriterError("全部记录被门禁剔除（无可导出数据）")

    lines: list[bytes] = []
    prov_lines: list[bytes] = []
    for idx, record in enumerate(kept):
        lines.append(_dump_line(_dpo_payload(record, conversational)))
        prov_lines.append(
            _provenance_line(
                idx,
                record.provenance,
                {
                    "presented_order": list(record.presented_order),
                    "rationale": record.rationale,
                    "criteria_scores": record.criteria_scores,
                    "confidence": record.confidence,
                },
            )
        )

    files = {
        "preference_dpo.jsonl": b"".join(lines),
        "provenance.jsonl": b"".join(prov_lines),
    }
    manifest = _build_manifest(
        DPO_FORMAT, files, len(kept), report, {"conversational": conversational}
    )
    return ExportBundle(files=files, manifest=manifest)


__all__ = [
    "ExportBundle",
    "ExclusionReason",
    "LlmExportWriterError",
    "export_dpo",
    "export_sft",
]
