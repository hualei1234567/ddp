"""上传完成流程的纯函数辅助。"""

from __future__ import annotations

from dataclasses import asdict

from app.application.ingestion.ports import (
    CompletePartSpec,
    CompleteUploadCommand,
    CreateUploadSessionCommand,
)
from app.domain.ingestion.enums import RawAssetFormat
from app.domain.ingestion.errors import DomainValidationError
from app.domain.ingestion.models import UploadSession
from app.domain.ingestion.validation import (
    normalize_etag,
    normalize_sha256,
    resolve_declared_format,
    validate_part_number,
)

MAX_PART_NUMBER = 10000


def declare_format(session: UploadSession) -> RawAssetFormat:
    if session.format_hint is not None:
        return RawAssetFormat(session.format_hint)
    return resolve_declared_format(session.declared_content_type, None)


def validate_complete_parts(
    parts: tuple[CompletePartSpec, ...],
) -> tuple[CompletePartSpec, ...]:
    if not parts:
        raise DomainValidationError("parts 不能为空")
    if len(parts) > MAX_PART_NUMBER:
        raise DomainValidationError("parts 数量超过上限")
    result: list[CompletePartSpec] = []
    previous = 0
    for spec in parts:
        validate_part_number(spec.part_number)
        if spec.part_number <= previous:
            raise DomainValidationError("parts 必须按 part_number 严格升序且不重复")
        previous = spec.part_number
        if spec.size_bytes <= 0:
            raise DomainValidationError("分片大小必须为正整数")
        normalize_etag(spec.etag)
        normalize_sha256(spec.sha256)
        result.append(spec)
    if result[0].part_number != 1:
        raise DomainValidationError("parts 必须从 part_number=1 开始")
    return tuple(result)


def command_body(command: CreateUploadSessionCommand) -> dict[str, object]:
    return asdict(command)


def complete_body(command: CompleteUploadCommand) -> dict[str, object]:
    return asdict(command)
