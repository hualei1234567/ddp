"""Episode 字段、时间基准、标定快照与对象 Key 校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from app.domain.episodes.errors import (
    DomainValidationError,
    TimeBaseInvalidError,
)

CODE_MAX_LENGTH = 64
VERSION_TEXT_MAX_LENGTH = 128
VERSION_TEXT_SHORT_MAX_LENGTH = 64
REASON_MAX_LENGTH = 2000
SCENE_LABEL_MAX_LENGTH = 100
OBJECT_KEY_MAX_LENGTH = 1024
SUPERSEDES_MAX_DEPTH = 5

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
MANIFEST_KEY_PATTERN = re.compile(
    rf"^canonical/{_UUID_PATTERN}/{_UUID_PATTERN}/[1-9][0-9]*/manifest\.json$"
)

TIME_DOMAINS = frozenset({"UTC", "ROS", "DEVICE", "HOST", "MONOTONIC", "UNKNOWN"})
CLOCK_SOURCES = frozenset(
    {"PTP", "NTP", "ROS_CLOCK", "DEVICE_CLOCK", "HOST_CLOCK", "MONOTONIC", "EXTERNAL", "UNKNOWN"}
)
SYNC_METHODS = frozenset({"PTP", "NTP", "HARDWARE_TRIGGER", "SOFTWARE_ESTIMATE", "MANUAL", "NONE"})
SYNC_STATUSES = frozenset({"VERIFIED", "DEGRADED", "UNVERIFIED", "FAILED"})
# 未通过时间同步校验的状态集合：fail-closed 的**唯一**权威定义。
# ``validate_time_base``（能否建 Episode）与自动就绪判定
# （``domain/episodes/readiness.py``，为什么还不能就绪）共用这一份，
# 避免两处各写一组"哪些状态算未同步"而慢慢漂移。
SYNC_STATUSES_UNVERIFIED = frozenset({"UNVERIFIED", "FAILED"})
CALIBRATION_TARGET_TYPES = frozenset({"ROBOT", "DEVICE", "SENSOR", "SYSTEM"})

_TIME_BASE_REQUIRED = ("time_domain", "clock_source", "sync_method", "sync_status")
_TIME_BASE_ALLOWED = frozenset(
    {
        "time_domain",
        "clock_source",
        "clock_id",
        "offset_to_utc_ns",
        "drift_ppm",
        "uncertainty_ns",
        "sync_method",
        "sync_ref",
        "sync_status",
    }
)
_CALIBRATION_REQUIRED = (
    "calibration_id",
    "calibration_version",
    "checksum",
    "target_type",
    "target_id",
    "schema_version",
)


def required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} 必须是非空字符串")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field_name} 长度不得超过 {max_length}")
    return cleaned


def optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return required_text(value, field_name, max_length=max_length)


def optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID 或 null")
    return value


def uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple | set | frozenset):
        raise DomainValidationError(f"{field_name} 必须是 UUID 列表")
    result: list[UUID] = []
    seen: set[UUID] = set()
    for item in value:
        if not isinstance(item, UUID):
            raise DomainValidationError(f"{field_name} 只能包含 UUID")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def int64(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{field_name} 必须是整数")
    if not INT64_MIN <= value <= INT64_MAX:
        raise DomainValidationError(f"{field_name} 超出 int64 范围")
    return value


def aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区（UTC）")
    return value.astimezone(UTC)


def optional_aware_datetime(value: object, field_name: str) -> datetime | None:
    return None if value is None else aware_datetime(value, field_name)


def time_range(start_time_ns: object, end_time_ns: object) -> tuple[int, int]:
    start = int64(start_time_ns, "start_time_ns")
    end = int64(end_time_ns, "end_time_ns")
    if start >= end:
        raise DomainValidationError("start_time_ns 必须小于 end_time_ns")
    return start, end


def validate_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value.strip()) is None:
        raise DomainValidationError(f"{field_name} 必须为 sha256:<64 位小写十六进制>")
    return value.strip()


def validate_time_base(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TimeBaseInvalidError("time_base 必须是对象")
    unknown = set(value) - _TIME_BASE_ALLOWED
    if unknown:
        raise TimeBaseInvalidError("time_base 包含未声明字段")
    missing = [key for key in _TIME_BASE_REQUIRED if key not in value]
    if missing:
        raise TimeBaseInvalidError("time_base 缺少必要字段")
    time_domain = value["time_domain"]
    if time_domain not in TIME_DOMAINS:
        raise TimeBaseInvalidError("time_base.time_domain 取值非法")
    clock_source = value["clock_source"]
    if clock_source not in CLOCK_SOURCES:
        raise TimeBaseInvalidError("time_base.clock_source 取值非法")
    sync_method = value["sync_method"]
    if sync_method not in SYNC_METHODS:
        raise TimeBaseInvalidError("time_base.sync_method 取值非法")
    sync_status = value["sync_status"]
    if sync_status not in SYNC_STATUSES:
        raise TimeBaseInvalidError("time_base.sync_status 取值非法")
    if sync_status in SYNC_STATUSES_UNVERIFIED:
        raise TimeBaseInvalidError("时间同步未验证或失败，禁止创建 Episode")
    return _json_copy(value, "time_base")


def validate_calibration_snapshot(value: object) -> tuple[dict[str, object], ...]:
    """校验标定快照；**允许为空数组**。

    为什么允许空：契约 ``EpisodeCreate.calibration_snapshot`` 明确
    ``default: []`` 且不在 ``required`` 里——允许调用方先建 Episode 再补标定，
    也允许数据源本身不含标定信息（例如 Hugging Face 公开第一人称数据集，
    真实元数据里只有视频与任务字段，没有任何相机内参或机械臂标定）。

    此前这里强制非空并抛 ``calibration_not_active``，造成两个后果：
    1. 契约允许的请求被 422 拒绝（实现比契约更严，客户端按契约写就会失败）；
    2. 数据集侧的 ``CALIBRATION_MISSING`` 排除码永远不可达——空快照在
       创建 Episode 时就被挡住了，根本走不到选择阶段。

    放宽后两条门禁各归其位：
    - **能否创建 Episode**：只看字段结构（这里）；
    - **能否进入数据集**：``domain/datasets/selection.py`` 按快照是否为空
      区分 ``CALIBRATION_MISSING``（没有）与 ``CALIBRATION_INVALID``（有但无效），
      由数据集策略决定是否放行——这才是"数据准入"该管的判断。

    非空时仍然逐项严格校验，不做任何放宽。
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise DomainValidationError("calibration_snapshot 必须是数组")
    snapshots: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DomainValidationError("calibration_snapshot 只能包含对象")
        if set(item) != set(_CALIBRATION_REQUIRED):
            raise DomainValidationError("calibration_snapshot 字段不符合契约")
        calibration_id = _uuid_text(item["calibration_id"], "calibration_id")
        target_id = _uuid_text(item["target_id"], "target_id")
        version = positive_int(item["calibration_version"], "calibration_version")
        checksum = validate_sha256(item["checksum"], "checksum")
        target_type = item["target_type"]
        if target_type not in CALIBRATION_TARGET_TYPES:
            raise DomainValidationError("calibration_snapshot.target_type 取值非法")
        schema_version = required_text(
            item["schema_version"],
            "schema_version",
            max_length=VERSION_TEXT_SHORT_MAX_LENGTH,
        )
        snapshots.append(
            {
                "calibration_id": calibration_id,
                "calibration_version": version,
                "checksum": checksum,
                "target_type": target_type,
                "target_id": target_id,
                "schema_version": schema_version,
            }
        )
    return tuple(snapshots)


def validate_scene_labels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple | set | frozenset):
        raise DomainValidationError("scene_labels 必须是字符串数组")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        label = required_text(item, "scene_labels", max_length=SCENE_LABEL_MAX_LENGTH)
        if label not in seen:
            seen.add(label)
            result.append(label)
    return tuple(result)


def build_manifest_key(project_id: UUID, episode_id: UUID, episode_version: int) -> str:
    key = f"canonical/{project_id}/{episode_id}/{episode_version}/manifest.json"
    validate_manifest_key(key)
    return key


def validate_manifest_key(value: object) -> str:
    if not isinstance(value, str) or len(value) > OBJECT_KEY_MAX_LENGTH:
        raise DomainValidationError("manifest_key 不符合规范")
    if MANIFEST_KEY_PATTERN.fullmatch(value) is None:
        raise DomainValidationError("manifest_key 不符合规范")
    return value


def _uuid_text(value: object, field_name: str) -> str:
    normalized: UUID
    if isinstance(value, UUID):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = UUID(value.strip())
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 必须是 UUID") from exc
    else:
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return str(normalized)


def _json_copy(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    copied = {str(key): item for key, item in value.items()}
    try:
        json.dumps(copied, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 对象") from exc
    return copied
