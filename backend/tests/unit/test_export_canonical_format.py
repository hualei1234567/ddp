"""Canonical 导出格式（V31-041）：格式选择与产物差异。

与 ``test_export_domain.py`` 的分工：那个文件验证 LeRobot 元数据产物的内容；
本文件验证**新增的 CANONICAL 格式**——它必须与 LeRobot 产物**可区分**，
且不能把"元数据 profile"伪装成"可被 Loader 加载的真实格式"。

**为什么需要独立格式**：LeRobot 产物明确标注 ``frame_level_mapping: false`` /
``verification_status: UNVERIFIED``（见 ``exporting.py`` 模块文档）。Canonical
是平台自己的规范格式，语义不同——混用同一个 ``ExportFormat`` 会让下游无法
判断"这份产物能不能直接喂给 LeRobot Loader"。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.datasets.enums import ExportFormat, Split
from app.domain.datasets.export_jobs import new_export_job
from app.domain.datasets.exporting import build_export_artifacts
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.policies import PolicyRef

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
PROJECT = UUID(int=1)


def _ref(seed: str) -> PolicyRef:
    return PolicyRef(policy_id=UUID(int=int(seed, 16)), version=1, policy_sha256=SHA)


def _job(*, export_format: ExportFormat) -> object:
    return new_export_job(
        export_job_id=UUID(int=10),
        project_id=PROJECT,
        dataset_version_id=UUID(int=20),
        format=export_format,
        format_version=(
            "ddp.lerobot/1.0.0" if export_format is ExportFormat.LE_ROBOT else "ddp.canonical/1.0.0"
        ),
        exporter_id="ddp.exporter",
        exporter_version="1.0.0",
        mapping_version="ddp.mapping/1.0.0",
        config={},
        idempotency_key="export-key-0001",
        requested_by=UUID(int=30),
        output_prefix="exports/p/20/10/",
        now=NOW,
        timeout_seconds=60,
        max_attempts=3,
    )


def _version() -> DatasetVersion:
    return DatasetVersion(
        dataset_version_id=UUID(int=20),
        project_id=PROJECT,
        dataset_id=UUID(int=21),
        dataset_lineage_id=UUID(int=22),
        dataset_version=1,
        selection_rule_version="dataset-selection/1",
        selection_filters={},
        selection_snapshot_at=NOW,
        dedup_policy_ref=_ref("31"),
        split_policy_ref=_ref("32"),
        leakage_policy_ref=_ref("33"),
        quality_policy_ref=_ref("34"),
        status="FROZEN",
        validity_status="VALID",
        item_count=2,
        train_count=1,
        validation_count=1,
        test_count=0,
        episode_version_digest=SHA,
        manifest_sha256=SHA_B,
        quality_report_sha256=SHA_C,
        leakage_report_sha256=SHA,
        frozen_at=NOW,
        frozen_by=UUID(int=30),
        freeze_approval_id=UUID(int=40),
        created_at=NOW,
        updated_at=NOW,
        created_by=UUID(int=30),
        updated_by=UUID(int=30),
    )


def _item() -> DatasetItem:
    return DatasetItem(
        dataset_item_id=UUID(int=100),
        project_id=PROJECT,
        dataset_version_id=UUID(int=20),
        episode_id=UUID(int=200),
        episode_version=1,
        episode_lineage_id=UUID(int=300),
        manifest_sha256=SHA,
        raw_asset_ids=(UUID(int=400),),
        annotation_submission_id=UUID(int=500),
        annotation_version=1,
        annotation_record_digest=SHA_B,
        schema_id=UUID(int=600),
        schema_version=1,
        schema_sha256=SHA_C,
        split=Split.TRAIN,
        created_at=NOW,
    )


def test_canonical_format_is_a_distinct_enum_value() -> None:
    """Canonical 与 LeRobot 是两个格式，不能混用。"""
    assert ExportFormat.CANONICAL.value == "CANONICAL"
    assert ExportFormat.CANONICAL is not ExportFormat.LE_ROBOT


def test_canonical_job_accepts_its_own_format_version() -> None:
    """``format_version`` 必须与 ``format`` 匹配；交叉组合被拒绝。"""
    job = _job(export_format=ExportFormat.CANONICAL)
    assert job.format is ExportFormat.CANONICAL
    assert job.format_version == "ddp.canonical/1.0.0"


def test_canonical_rejects_lerobot_format_version() -> None:
    with pytest.raises(Exception, match="必须使用版本"):
        new_export_job(
            export_job_id=UUID(int=10),
            project_id=PROJECT,
            dataset_version_id=UUID(int=20),
            format=ExportFormat.CANONICAL,
            format_version="ddp.lerobot/1.0.0",
            exporter_id="ddp.exporter",
            exporter_version="1.0.0",
            mapping_version="ddp.mapping/1.0.0",
            config={},
            idempotency_key="export-key-0001",
            requested_by=UUID(int=30),
            output_prefix="exports/p/20/10/",
            now=NOW,
            timeout_seconds=60,
            max_attempts=3,
        )


def test_canonical_artifacts_declare_their_own_format() -> None:
    """产物里的 ``format`` 字段必须如实反映任务格式，不能写死 LE_ROBOT。"""
    artifacts = build_export_artifacts(
        _job(export_format=ExportFormat.CANONICAL),
        _version(),
        (_item(),),
        created_at=NOW,
    )
    info = next(item for item in artifacts if item.path == "meta/info.json")
    payload = json.loads(info.data)
    assert payload["format"] == "CANONICAL"
    assert payload["format_version"] == "ddp.canonical/1.0.0"


def test_canonical_sidecar_does_not_claim_lerobot_verification() -> None:
    """Canonical 不是 LeRobot 产物，sidecar 不得复用 LeRobot 的验证口径。"""
    artifacts = build_export_artifacts(
        _job(export_format=ExportFormat.CANONICAL),
        _version(),
        (_item(),),
        created_at=NOW,
    )
    sidecar = next(item for item in artifacts if item.path == "ddp/sidecar.json")
    payload = json.loads(sidecar.data)
    assert payload["format"] == "CANONICAL"
    assert payload["frame_level_mapping"] is False
