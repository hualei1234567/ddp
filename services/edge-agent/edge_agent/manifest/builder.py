"""Manifest 构建与校验。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from edge_agent.errors import ManifestError
from edge_agent.manifest.canonical import HASH_FIELD, canonical_json, compute_manifest_hash
from edge_agent.manifest.models import (
    ArtifactInfo,
    CalibrationRef,
    ClockInfo,
    Manifest,
    ManifestContext,
    ManifestWarning,
    StreamInfo,
)
from edge_agent.manifest.validation import (
    ALLOWED_CLOCK_UNITS,
    ALLOWED_FORMATS,
    assert_no_forbidden_content,
    normalize_optional_uuid,
    normalize_sha256,
    normalize_time,
    normalize_uuid,
    validate_mime,
    validate_relative_path,
)

MANIFEST_SCHEMA_VERSION = "ddp.edge-agent.manifest/1"


def _uuid_list(values: Sequence[str], name: str) -> list[str]:
    return sorted({normalize_uuid(v, name) for v in values})


def _artifact_doc(item: ArtifactInfo) -> dict[str, Any]:
    if item.size_bytes <= 0:
        raise ManifestError(f"artifact 大小必须 > 0：{item.path}")
    fmt = item.format.upper()
    if fmt not in ALLOWED_FORMATS:
        raise ManifestError(f"artifact 格式不在允许集合：{item.format!r}")
    if item.duration_ms is not None and (
        isinstance(item.duration_ms, bool) or item.duration_ms < 0
    ):
        raise ManifestError(f"artifact duration_ms 非法：{item.path}")
    return {
        "path": validate_relative_path(item.path, "artifact.path"),
        "mime_type": validate_mime(item.mime_type, "artifact.mime_type"),
        "size_bytes": item.size_bytes,
        "sha256": normalize_sha256(item.sha256, "artifact.sha256"),
        "duration_ms": item.duration_ms,
        "format": fmt,
    }


def _clock_doc(clock: ClockInfo) -> dict[str, Any]:
    if not clock.source:
        raise ManifestError("clock.source 不能为空")
    if clock.unit not in ALLOWED_CLOCK_UNITS:
        raise ManifestError(f"clock.unit 非法：{clock.unit!r}")
    return {"source": clock.source, "unit": clock.unit, "sync": dict(clock.sync)}


def _stream_doc(stream: StreamInfo, known_paths: set[str]) -> dict[str, Any]:
    if not stream.stream_id or not stream.kind:
        raise ManifestError("stream_id / kind 不能为空")
    paths = sorted(
        validate_relative_path(p, "stream.artifact_paths") for p in stream.artifact_paths
    )
    missing = [p for p in paths if p not in known_paths]
    if missing:
        raise ManifestError(f"stream {stream.stream_id} 引用了不存在的 artifact：{missing}")
    return {
        "stream_id": stream.stream_id,
        "sensor_id": normalize_optional_uuid(stream.sensor_id, "stream.sensor_id"),
        "kind": stream.kind,
        "artifact_paths": paths,
        "metadata": dict(stream.metadata),
    }


def _calibration_doc(item: CalibrationRef) -> dict[str, str]:
    if not item.version:
        raise ManifestError("calibration.version 不能为空")
    return {
        "calibration_id": normalize_uuid(item.calibration_id, "calibration_id"),
        "version": item.version,
    }


def build_manifest(
    context: ManifestContext,
    artifacts: Sequence[ArtifactInfo],
    warnings: Sequence[ManifestWarning] = (),
) -> Manifest:
    """生成确定性 Manifest：集合型字段排序去重，时间统一为 UTC 微秒精度。"""
    if not artifacts:
        raise ManifestError("Manifest 至少需要一个 artifact")
    artifact_docs = sorted((_artifact_doc(a) for a in artifacts), key=lambda d: d["path"])
    paths = [d["path"] for d in artifact_docs]
    if len(paths) != len(set(paths)):
        raise ManifestError("artifact path 重复")
    start = normalize_time(context.start_time, "start_time")
    end = normalize_time(context.end_time, "end_time")
    if start is not None and end is not None and end < start:
        raise ManifestError("end_time 早于 start_time")
    if not context.agent_id or not context.agent_version:
        raise ManifestError("agent_id / agent_version 不能为空")

    document: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "agent": {"agent_id": context.agent_id, "version": context.agent_version},
        "capture_session_id": normalize_uuid(context.capture_session_id, "capture_session_id"),
        "collection_task_id": normalize_optional_uuid(
            context.collection_task_id, "collection_task_id"
        ),
        "project_id": normalize_uuid(context.project_id, "project_id"),
        "robot_id": normalize_optional_uuid(context.robot_id, "robot_id"),
        "device_ids": _uuid_list(context.device_ids, "device_id"),
        "sensor_ids": _uuid_list(context.sensor_ids, "sensor_id"),
        "calibrations": sorted(
            (_calibration_doc(c) for c in context.calibrations),
            key=lambda d: (d["calibration_id"], d["version"]),
        ),
        "start_time": start,
        "end_time": end,
        "clock": _clock_doc(context.clock),
        "adapter": {"name": context.adapter_name, "version": context.adapter_version},
        "streams": sorted(
            (_stream_doc(s, set(paths)) for s in context.streams), key=lambda d: d["stream_id"]
        ),
        "artifacts": artifact_docs,
        "warnings": sorted(
            (
                {
                    "code": w.code,
                    "message": w.message,
                    "artifact_path": (
                        None
                        if w.artifact_path is None
                        else validate_relative_path(w.artifact_path, "warning.artifact_path")
                    ),
                }
                for w in warnings
            ),
            key=lambda d: (d["code"], d["artifact_path"] or "", d["message"]),
        ),
    }
    assert_no_forbidden_content(document)
    manifest_hash = compute_manifest_hash(document)
    document[HASH_FIELD] = manifest_hash
    return Manifest(
        document=document, canonical_bytes=canonical_json(document), manifest_hash=manifest_hash
    )


def verify_manifest_document(document: Mapping[str, Any]) -> str:
    """校验已落盘/收到的 Manifest：禁止内容 + hash 自洽。返回其 manifest_hash。"""
    assert_no_forbidden_content(document)
    declared = document.get(HASH_FIELD)
    if not isinstance(declared, str):
        raise ManifestError("Manifest 缺少 manifest_hash")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(f"不支持的 schema_version：{document.get('schema_version')!r}")
    if compute_manifest_hash(document) != declared:
        raise ManifestError("manifest_hash 与内容不一致")
    return declared


def load_manifest(raw: bytes) -> Manifest:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Manifest 不是合法 JSON：{exc}") from exc
    if not isinstance(document, dict):
        raise ManifestError("Manifest 顶层必须是对象")
    manifest_hash = verify_manifest_document(document)
    return Manifest(
        document=document, canonical_bytes=canonical_json(document), manifest_hash=manifest_hash
    )
