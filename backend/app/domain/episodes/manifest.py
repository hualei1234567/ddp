"""Episode Manifest 规范化与 Hash 校验。

Manifest 由 Worker 生成、服务端计算 Hash；客户端提交的 Hash 一律不作为权威。
规范化采用 JSON Canonicalization Scheme 的可控子集：对象键排序、无空白、
拒绝浮点 NaN/Infinity 与 ``manifest_sha256`` 自引用字段。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID

from app.domain.episodes.errors import (
    ManifestHashMismatchError,
    ManifestSchemaInvalidError,
)

MANIFEST_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "episode_id",
        "episode_lineage_id",
        "episode_version",
        "project_id",
        "code",
        "task_id",
        "batch_id",
        "robot_id",
        "device_ids",
        "sensor_ids",
        "start_time",
        "end_time",
        "duration_ns",
        "time_base",
        "time_alignment",
        "calibration_snapshot",
        "streams",
        "raw_asset_ids",
        "provenance",
        "quality",
    }
)
MANIFEST_IDENTITY_KEYS = (
    "episode_id",
    "episode_lineage_id",
    "episode_version",
    "project_id",
    "code",
)


def canonical_manifest_json(manifest: Mapping[str, object]) -> str:
    if "manifest_sha256" in manifest:
        raise ManifestSchemaInvalidError("Manifest 不得包含自身 manifest_sha256")
    _reject_non_canonical(manifest, depth=0)
    try:
        return json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ManifestSchemaInvalidError("Manifest 必须是合法 JSON 对象") from exc


def compute_manifest_sha256(manifest: Mapping[str, object]) -> str:
    canonical = canonical_manifest_json(manifest)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def validate_manifest_identity(
    manifest: object,
    *,
    episode_id: UUID,
    episode_lineage_id: UUID,
    episode_version: int,
    project_id: UUID,
    code: str,
    start_time_ns: int,
    end_time_ns: int,
    duration_ns: int,
) -> None:
    if not isinstance(manifest, Mapping):
        raise ManifestSchemaInvalidError("Manifest 必须是对象")
    missing = sorted(MANIFEST_REQUIRED_KEYS - set(manifest))
    if missing:
        raise ManifestSchemaInvalidError(
            "Manifest 缺少必要字段",
            details={"missing": missing},
        )
    expected = {
        "episode_id": str(episode_id),
        "episode_lineage_id": str(episode_lineage_id),
        "episode_version": episode_version,
        "project_id": str(project_id),
        "code": code,
    }
    for key, value in expected.items():
        actual = manifest.get(key)
        if key == "episode_version":
            if actual != value:
                raise ManifestSchemaInvalidError(f"Manifest 的 {key} 与 Episode 不一致")
        else:
            if str(actual) != value:
                raise ManifestSchemaInvalidError(f"Manifest 的 {key} 与 Episode 不一致")
    start = _manifest_int(manifest.get("start_time"), "start_time")
    end = _manifest_int(manifest.get("end_time"), "end_time")
    duration = _manifest_int(manifest.get("duration_ns"), "duration_ns")
    if (start, end, duration) != (start_time_ns, end_time_ns, duration_ns):
        raise ManifestSchemaInvalidError("Manifest 时间范围与 Episode 不一致")
    if not isinstance(manifest.get("time_base"), Mapping):
        raise ManifestSchemaInvalidError("Manifest 缺少合法 time_base")
    if not isinstance(manifest.get("calibration_snapshot"), list):
        raise ManifestSchemaInvalidError("Manifest 缺少 calibration_snapshot 数组")
    if not isinstance(manifest.get("streams"), list):
        raise ManifestSchemaInvalidError("Manifest 缺少 streams 数组")
    if not isinstance(manifest.get("raw_asset_ids"), list):
        raise ManifestSchemaInvalidError("Manifest 缺少 raw_asset_ids 数组")
    if not isinstance(manifest.get("provenance"), Mapping):
        raise ManifestSchemaInvalidError("Manifest 缺少 provenance 对象")
    if not isinstance(manifest.get("quality"), Mapping):
        raise ManifestSchemaInvalidError("Manifest 缺少 quality 对象")


def assert_manifest_hash(manifest: Mapping[str, object], expected_sha256: str) -> None:
    actual = compute_manifest_sha256(manifest)
    if actual != expected_sha256:
        raise ManifestHashMismatchError(
            "Manifest Hash 校验失败",
            details={"expected": expected_sha256, "actual": actual},
        )


def _manifest_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ManifestSchemaInvalidError(f"Manifest 的 {field_name} 必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 10)
        except ValueError as exc:
            raise ManifestSchemaInvalidError(f"Manifest 的 {field_name} 必须是十进制整数") from exc
    raise ManifestSchemaInvalidError(f"Manifest 的 {field_name} 必须是整数")


def _reject_non_canonical(value: object, *, depth: int) -> None:
    if depth > 16:
        raise ManifestSchemaInvalidError("Manifest 嵌套过深")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ManifestSchemaInvalidError("Manifest 对象键必须是字符串")
        for item in value.values():
            _reject_non_canonical(item, depth=depth + 1)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_non_canonical(item, depth=depth + 1)
        return
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        # Manifest 数值必须是可精确规范化的整数；浮点不在本阶段契约内。
        raise ManifestSchemaInvalidError("Manifest 不允许浮点数")
    raise ManifestSchemaInvalidError("Manifest 包含不支持的 JSON 类型")
