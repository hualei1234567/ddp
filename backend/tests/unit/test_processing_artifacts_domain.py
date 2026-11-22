"""``ProcessingArtifact`` 产物目录领域测试（V31-016）。

产物目录把「运行产出了哪些对象」从 ``output_refs`` 的裸字符串数组升级为
一等实体：每条记录带阶段、尝试、产物类型、对象 key、Schema 版本、内容类型、
字节数与内容摘要。

**为什么不是 ``output_refs`` 的投影**：``output_refs`` 只有 key，无法回答
"这个产物是哪个阶段、哪次尝试、什么类型、多大、摘要是否一致"。产物目录
是权威记录，``output_refs`` 退化为兼容投影。

**为什么不新建 ``Artifact`` 通用实体**：接入域的
``CaptureSessionArtifact`` 是只读视图（不落库），数据集域的导出产物走
``export_jobs``；处理运行的产物有自己的生命周期（随运行级联删除）与
阶段归属，强行合并会让三处职责互相污染。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.processing.artifacts import (
    ARTIFACT_OBJECT_KEY_MAX_LENGTH,
    ARTIFACT_SCHEMA_VERSION_MAX_LENGTH,
    ProcessingArtifact,
    ProcessingArtifactKind,
)
from app.domain.processing.enums import ProcessingStage
from app.domain.processing.errors import DomainValidationError

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def _artifact(**overrides: object) -> ProcessingArtifact:
    values: dict[str, object] = {
        "artifact_id": uuid4(),
        "project_id": uuid4(),
        "run_id": uuid4(),
        "stage": ProcessingStage.METADATA_EXTRACTION.value,
        "attempt": 1,
        "kind": ProcessingArtifactKind.METADATA.value,
        "object_key": "processing-run/p/r/metadata/0000-mp4.json",
        "schema_version": "ddp.processing.metadata/1.0.0",
        "content_type": "application/json",
        "size_bytes": 512,
        "checksum_sha256": "a" * 64,
        "source_ref": "raw/p/asset.mp4",
        "created_at": NOW,
        "created_by": uuid4(),
    }
    values.update(overrides)
    return ProcessingArtifact(**values)  # type: ignore[arg-type]


def test_artifact_accepts_complete_record() -> None:
    artifact = _artifact()

    assert artifact.kind == ProcessingArtifactKind.METADATA.value
    assert artifact.stage == ProcessingStage.METADATA_EXTRACTION.value
    assert artifact.size_bytes == 512
    assert artifact.checksum_sha256 == "a" * 64


def test_artifact_kind_must_be_known() -> None:
    with pytest.raises(DomainValidationError, match="kind 取值非法"):
        _artifact(kind="NOT_A_KIND")


def test_artifact_stage_must_be_known() -> None:
    with pytest.raises(DomainValidationError, match="stage 取值非法"):
        _artifact(stage="NOT_A_STAGE")


def test_artifact_rejects_empty_object_key() -> None:
    with pytest.raises(DomainValidationError, match="object_key 不能为空"):
        _artifact(object_key="   ")


def test_artifact_rejects_object_key_over_limit() -> None:
    with pytest.raises(DomainValidationError, match="object_key 超过最大长度"):
        _artifact(object_key="k" * (ARTIFACT_OBJECT_KEY_MAX_LENGTH + 1))


def test_artifact_rejects_schema_version_over_limit() -> None:
    with pytest.raises(DomainValidationError, match="schema_version 超过最大长度"):
        _artifact(schema_version="v" * (ARTIFACT_SCHEMA_VERSION_MAX_LENGTH + 1))


def test_artifact_rejects_negative_size() -> None:
    with pytest.raises(DomainValidationError, match="size_bytes 必须是非负整数"):
        _artifact(size_bytes=-1)


def test_artifact_rejects_attempt_below_one() -> None:
    with pytest.raises(DomainValidationError, match="attempt 必须是正整数"):
        _artifact(attempt=0)


def test_artifact_checksum_must_be_sha256_hex_or_none() -> None:
    """摘要要么是 64 位小写十六进制，要么为空——不接受"看起来像"的短串。"""
    with pytest.raises(DomainValidationError, match="checksum_sha256 必须是 64 位小写十六进制"):
        _artifact(checksum_sha256="abc123")
    with pytest.raises(DomainValidationError, match="checksum_sha256 必须是 64 位小写十六进制"):
        _artifact(checksum_sha256="A" * 64)
    # 空摘要合法：部分阶段产物无稳定摘要（例如仅引用）。
    assert _artifact(checksum_sha256=None).checksum_sha256 is None


def test_artifact_requires_aware_created_at() -> None:
    with pytest.raises(DomainValidationError, match="created_at 必须是带时区时间"):
        _artifact(created_at=datetime(2026, 11, 22, 10, 0))


def test_artifact_kind_enum_covers_every_object_storage_product() -> None:
    """产物类型只覆盖**对象存储中的产物**。

    ``QUALITY_PRE_CHECK`` 的 ``quality-run/<id>`` 是数据库记录引用，不是对象
    存储对象，因此不在产物目录里——否则"产物"会同时指两种东西。
    """
    assert {kind.value for kind in ProcessingArtifactKind} == {
        "METADATA",
        "CANONICAL",
        "TIME_ALIGNMENT_REPORT",
    }
