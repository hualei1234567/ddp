"""上传、内容签名与对象 Key 校验。

只覆盖上传/资产域自身规则；不提供跨模块通用工具。错误文案不拼接客户端原始值。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.domain.ingestion.enums import RawAssetFormat
from app.domain.ingestion.errors import (
    DomainValidationError,
    FileTypeUnsupportedError,
    InvalidFilenameError,
)

FILENAME_MAX_LENGTH = 255
CONTENT_TYPE_MAX_LENGTH = 200
OBJECT_KEY_MAX_LENGTH = 1024
ETAG_MAX_LENGTH = 256
PART_NUMBER_MIN = 1
PART_NUMBER_MAX = 10000
JSON_MAX_BYTES = 64 * 1024
JSON_MAX_KEYS = 64
JSON_MAX_DEPTH = 8

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
RAW_CONTENT_KEY_PATTERN = re.compile(
    rf"^raw/{_UUID_PATTERN}/[0-9]{{4}}/[0-9]{{2}}/[0-9]{{2}}/"
    rf"{_UUID_PATTERN}/content\.[a-z0-9]{{1,16}}$"
)
_UPLOAD_SCRATCH_KEY_PATTERN = re.compile(rf"^tmp/uploads/{_UUID_PATTERN}/{_UUID_PATTERN}/scratch$")

# 声明内容类型白名单。application/octet-stream 是通用容器，实际格式以签名探测为准。
CONTENT_TYPE_FORMATS: Mapping[str, frozenset[RawAssetFormat]] = {
    "application/octet-stream": frozenset(
        {
            RawAssetFormat.MCAP,
            RawAssetFormat.ROSBAG2,
            RawAssetFormat.HDF5,
            RawAssetFormat.PARQUET,
        }
    ),
    "application/x-mcap": frozenset({RawAssetFormat.MCAP}),
    "application/x-rosbag2": frozenset({RawAssetFormat.ROSBAG2}),
    "application/x-hdf5": frozenset({RawAssetFormat.HDF5}),
    "video/mp4": frozenset({RawAssetFormat.MP4}),
    "video/quicktime": frozenset({RawAssetFormat.MP4}),
    "application/vnd.apache.parquet": frozenset({RawAssetFormat.PARQUET}),
    "text/csv": frozenset({RawAssetFormat.CSV}),
    "application/json": frozenset({RawAssetFormat.JSON}),
    "application/zip": frozenset({RawAssetFormat.ZIP}),
}

FORMAT_CONTENT_TYPES: Mapping[RawAssetFormat, str] = {
    RawAssetFormat.MCAP: "application/x-mcap",
    RawAssetFormat.ROSBAG2: "application/x-rosbag2",
    RawAssetFormat.HDF5: "application/x-hdf5",
    RawAssetFormat.MP4: "video/mp4",
    RawAssetFormat.PARQUET: "application/vnd.apache.parquet",
    RawAssetFormat.CSV: "text/csv",
    RawAssetFormat.JSON: "application/json",
    RawAssetFormat.ZIP: "application/zip",
    RawAssetFormat.UNKNOWN: "application/octet-stream",
}

FORMAT_EXTENSIONS: Mapping[RawAssetFormat, str] = {
    RawAssetFormat.MCAP: "mcap",
    RawAssetFormat.ROSBAG2: "db3",
    RawAssetFormat.HDF5: "h5",
    RawAssetFormat.MP4: "mp4",
    RawAssetFormat.PARQUET: "parquet",
    RawAssetFormat.CSV: "csv",
    RawAssetFormat.JSON: "json",
    RawAssetFormat.ZIP: "zip",
    RawAssetFormat.UNKNOWN: "bin",
}

DEFAULT_ALLOWED_CONTENT_TYPES: tuple[str, ...] = tuple(CONTENT_TYPE_FORMATS)

_MCAP_MAGIC = b"\x89MCAP0\r\n"
_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
HDF5_MAX_SIGNATURE_OFFSET = 1024 * 1024
HDF5_DETECTION_BYTES = HDF5_MAX_SIGNATURE_OFFSET + len(_HDF5_MAGIC)
_SQLITE_MAGIC = b"SQLite format 3\x00"
_ZIP_MAGIC = b"PK\x03\x04"
_PARQUET_MAGIC = b"PAR1"


def sanitize_filename(value: object) -> str:
    """清洗展示文件名；拒绝路径分隔符、控制字符、NUL 与 ``..`` 段。"""
    if not isinstance(value, str):
        raise InvalidFilenameError("文件名必须是字符串")
    cleaned = value.strip()
    if not cleaned:
        raise InvalidFilenameError("文件名不能为空")
    if len(cleaned) > FILENAME_MAX_LENGTH:
        raise InvalidFilenameError(f"文件名长度不得超过 {FILENAME_MAX_LENGTH} 个字符")
    if cleaned in {".", ".."}:
        raise InvalidFilenameError("文件名不得为路径段")
    for char in cleaned:
        if ord(char) == 0 or ord(char) < 32 or ord(char) == 127:
            raise InvalidFilenameError("文件名包含控制字符")
        if char in {"/", "\\"}:
            raise InvalidFilenameError("文件名不得包含路径分隔符")
    return cleaned


def normalize_content_type(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("declared_content_type 必须是非空字符串")
    content_type = value.strip().lower()
    if len(content_type) > CONTENT_TYPE_MAX_LENGTH:
        raise DomainValidationError(
            f"declared_content_type 长度不得超过 {CONTENT_TYPE_MAX_LENGTH} 个字符"
        )
    if content_type not in allowed:
        raise FileTypeUnsupportedError("声明的内容类型不在白名单内")
    return content_type


def resolve_declared_format(
    content_type: str,
    format_hint: object,
) -> RawAssetFormat:
    if format_hint is None:
        formats = CONTENT_TYPE_FORMATS.get(content_type, frozenset())
        return next(iter(formats)) if len(formats) == 1 else RawAssetFormat.UNKNOWN
    if not isinstance(format_hint, str):
        raise DomainValidationError("format_hint 必须是字符串")
    try:
        hint = RawAssetFormat(format_hint.strip().upper())
    except ValueError as exc:
        raise DomainValidationError("format_hint 取值非法") from exc
    compatible = CONTENT_TYPE_FORMATS.get(content_type, frozenset())
    if (
        hint is not RawAssetFormat.UNKNOWN
        and content_type != "application/octet-stream"
        and hint not in compatible
    ):
        raise DomainValidationError("format_hint 与声明内容类型不一致")
    return hint


def normalize_sha256(value: object, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise DomainValidationError("sha256 不能为空")
        return None
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value.strip()):
        raise DomainValidationError("sha256 必须为 sha256:<64 位小写十六进制>")
    return value.strip()


def sha256_to_base64(value: str) -> str:
    normalized = normalize_sha256(value)
    assert normalized is not None
    return base64.b64encode(bytes.fromhex(normalized.removeprefix("sha256:"))).decode("ascii")


def sha256_from_base64(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DomainValidationError("分片校验头不是合法 base64") from exc
    if len(raw) != 32:
        raise DomainValidationError("分片校验头必须是 32 字节 SHA256")
    return f"sha256:{raw.hex()}"


def normalize_etag(value: object) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("etag 必须是字符串")
    etag = value.strip()
    if len(etag) >= 2 and etag[0] == etag[-1] == '"':
        etag = etag[1:-1]
    etag = etag.strip()
    if not etag or len(etag) > ETAG_MAX_LENGTH:
        raise DomainValidationError(f"etag 长度必须在 1 到 {ETAG_MAX_LENGTH} 之间")
    return etag


def required_part_count(declared_size_bytes: int, part_size_bytes: int) -> int:
    if declared_size_bytes <= 0 or part_size_bytes <= 0:
        raise DomainValidationError("分片与文件大小必须为正整数")
    return (declared_size_bytes + part_size_bytes - 1) // part_size_bytes


def validate_part_number(part_number: int) -> int:
    if isinstance(part_number, bool) or not isinstance(part_number, int):
        raise DomainValidationError("part_number 必须是整数")
    if not PART_NUMBER_MIN <= part_number <= PART_NUMBER_MAX:
        raise DomainValidationError(
            f"part_number 必须在 {PART_NUMBER_MIN} 到 {PART_NUMBER_MAX} 之间"
        )
    return part_number


def expected_part_size(
    part_number: int,
    declared_size_bytes: int,
    part_size_bytes: int,
    *,
    require_exists: bool = True,
) -> int:
    validate_part_number(part_number)
    total_parts = required_part_count(declared_size_bytes, part_size_bytes)
    if require_exists and part_number > total_parts:
        raise DomainValidationError("part_number 超出该文件的分片数量")
    start = (part_number - 1) * part_size_bytes
    if start >= declared_size_bytes:
        raise DomainValidationError("part_number 超出该文件的分片数量")
    return min(part_size_bytes, declared_size_bytes - start)


def build_raw_object_key(
    project_id: UUID,
    asset_id: UUID,
    created_at: datetime,
    raw_format: RawAssetFormat | str,
) -> str:
    """服务端生成对象 Key；不包含原始文件名或客户端路径。"""
    moment = created_at
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    try:
        extension = FORMAT_EXTENSIONS[RawAssetFormat(raw_format)]
    except ValueError as exc:
        raise DomainValidationError("不支持的对象格式") from exc
    key = (
        f"raw/{project_id}/"
        f"{moment.year:04d}/{moment.month:02d}/{moment.day:02d}/"
        f"{asset_id}/content.{extension}"
    )
    if not validate_raw_object_key(key):
        raise DomainValidationError("生成的对象 Key 不符合契约")
    return key


def build_upload_scratch_key(project_id: UUID, upload_session_id: UUID) -> str:
    key = f"tmp/uploads/{project_id}/{upload_session_id}/scratch"
    if not _UPLOAD_SCRATCH_KEY_PATTERN.fullmatch(key):
        raise DomainValidationError("生成的上传临时 Key 不符合契约")
    return key


def validate_raw_object_key(key: object) -> bool:
    if not isinstance(key, str) or len(key) > OBJECT_KEY_MAX_LENGTH:
        return False
    return RAW_CONTENT_KEY_PATTERN.fullmatch(key) is not None


def canonical_content_type(raw_format: RawAssetFormat | str) -> str:
    try:
        return FORMAT_CONTENT_TYPES[RawAssetFormat(raw_format)]
    except ValueError as exc:
        raise DomainValidationError("不支持的资产格式") from exc


def detect_format(prefix: bytes, suffix: bytes = b"") -> RawAssetFormat | None:
    """按内容签名探测格式；无法唯一判定返回 ``None``，不凭扩展名放行。"""
    if prefix.startswith(_MCAP_MAGIC):
        return RawAssetFormat.MCAP
    if _has_hdf5_signature(prefix):
        return RawAssetFormat.HDF5
    if prefix.startswith(_SQLITE_MAGIC):
        return RawAssetFormat.ROSBAG2
    if prefix.startswith(_ZIP_MAGIC):
        return RawAssetFormat.ZIP
    if prefix.startswith(_PARQUET_MAGIC) and suffix.endswith(_PARQUET_MAGIC):
        return RawAssetFormat.PARQUET
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return RawAssetFormat.MP4
    stripped = prefix.lstrip()
    if stripped[:1] in {b"{", b"["}:
        return RawAssetFormat.JSON
    if _looks_like_text_csv(prefix):
        return RawAssetFormat.CSV
    return None


def _has_hdf5_signature(prefix: bytes) -> bool:
    """按 HDF5 规范扫描 0 或 512 整数倍偏移的文件签名。"""
    limit = min(len(prefix), HDF5_DETECTION_BYTES)
    for offset in range(0, limit - len(_HDF5_MAGIC) + 1, 512):
        if prefix[offset : offset + len(_HDF5_MAGIC)] == _HDF5_MAGIC:
            return True
    return False


def _looks_like_text_csv(prefix: bytes) -> bool:
    if not prefix or b"\x00" in prefix:
        return False
    try:
        text = prefix.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return ("," in text or "\n" in text) and ":" not in text[:1]


def signature_compatible(
    declared: RawAssetFormat | str,
    detected: RawAssetFormat | None,
) -> bool:
    if detected is None:
        return False
    try:
        declared_format = RawAssetFormat(declared)
    except ValueError:
        return False
    if declared_format is RawAssetFormat.UNKNOWN:
        # 通用二进制容器：声明类型不含具体格式，以内容签名探测结果为准；
        # 但无法识别的内容仍然失败关闭。
        return detected is not None
    if declared_format is RawAssetFormat.ROSBAG2:
        return detected in {RawAssetFormat.ROSBAG2, RawAssetFormat.MCAP}
    return declared_format is detected


def canonical_json(value: object, field_name: str) -> dict[str, object]:
    """校验并复制受限 JSON 对象；禁止 NaN/Infinity、过深与超大。"""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    key_count = _validate_json_value(value, field_name, depth=0)
    if key_count > JSON_MAX_KEYS:
        raise DomainValidationError(f"{field_name} 的对象键总数不得超过 {JSON_MAX_KEYS} 个")
    copied = _copy_json_value(value)
    try:
        encoded = json.dumps(copied, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 对象") from exc
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后不得超过 {JSON_MAX_BYTES} 字节")
    return copied  # type: ignore[return-value]


def _validate_json_value(value: object, field_name: str, *, depth: int) -> int:
    if depth > JSON_MAX_DEPTH:
        raise DomainValidationError(f"{field_name} 的 JSON 嵌套深度不得超过 {JSON_MAX_DEPTH} 层")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise DomainValidationError(f"{field_name} 的 JSON 键必须是字符串")
        return len(value) + sum(
            _validate_json_value(item, field_name, depth=depth + 1) for item in value.values()
        )
    if isinstance(value, list | tuple):
        return sum(_validate_json_value(item, field_name, depth=depth + 1) for item in value)
    if value is None or isinstance(value, bool | int | str):
        return 0
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise DomainValidationError(f"{field_name} 不允许 NaN 或 Infinity")
        return 0
    raise DomainValidationError(f"{field_name} 包含不支持的 JSON 类型")


def _copy_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_json_value(item) for item in value]
    return value
