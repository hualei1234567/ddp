"""业务扩展指标（N8-004）：存在性、数值口径与查询失败降级。

口径定义见 ``app/core/metrics.py`` 的模块注释与
``app/infrastructure/repositories/observability.py``。这里用真实 SQLite
（``metrics_app`` 夹具）验证"库里的行 → /metrics 文本里的数值"。

标签与跨项目泄露的全局白名单断言在 ``tests/api/test_metrics_api.py``，本文件
只补充新指标自身的样本与口径。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.datasources.enums import ImportStatus
from app.domain.quality.enums import QualityRunStatus
from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.db.models.datasources import DataSourceImportModel, DataSourceModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.processing import ProcessingRunModel
from app.infrastructure.db.models.quality import QualityRunModel
from app.infrastructure.repositories import observability
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds

_SHA = "sha256:" + "a" * 64
_RULE_SET = "sha256:" + "b" * 64


def _sample(body: str, prefix: str) -> float | None:
    """按样本行前缀取值；缺失时返回 ``None``（用于断言"没有样本"）。"""
    for line in body.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    return None


def _status(body: str, metric: str, status: str) -> float | None:
    return _sample(body, f'{metric}{{status="{status}"}} ')


def _seed_business_rows(
    factory: sessionmaker[Session],
    ids: ApiIds,
) -> datetime:
    """写入各指标口径所需的行；返回"最近一次成功导入"的完成时间。"""
    now = datetime.now(UTC)
    last_success = now - timedelta(hours=1)
    with factory() as session:
        source_id = uuid4()
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=ids.project_a,
                code=f"hf-{source_id.hex[:8]}",
                name="指标测试源",
                kind="HUGGINGFACE",
                repo_id="ddp/metrics-fixture",
                revision="c" * 40,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()

        for status, finished_at in (
            (ImportStatus.SUCCEEDED.value, last_success),
            (ImportStatus.SUCCEEDED.value, now - timedelta(days=2)),
            (ImportStatus.FAILED.value, None),
            (ImportStatus.PENDING.value, None),
        ):
            session.add(
                DataSourceImportModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    data_source_id=source_id,
                    status=status,
                    scope="SAMPLE",
                    tool_name="huggingface_hub",
                    tool_version="0.27.1",
                    sample_limit=1,
                    finished_at=finished_at,
                    created_by=ids.editor_a,
                    updated_by=ids.editor_a,
                )
            )

        session.add_all(
            [
                ProcessingRunModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    pipeline_version="v1",
                    input_refs=[],
                    status="CREATED",
                    created_by=ids.editor_a,
                    updated_by=ids.editor_a,
                ),
                ProcessingRunModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    pipeline_version="v1",
                    input_refs=[],
                    status="QUEUED",
                    created_by=ids.editor_a,
                    updated_by=ids.editor_a,
                ),
            ]
        )

        for status, score in (
            (QualityRunStatus.PASSED.value, 1.0),
            (QualityRunStatus.FAILED.value, 0.2),
            (QualityRunStatus.INCOMPLETE.value, None),
        ):
            session.add(
                QualityRunModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    subject_type="EPISODE",
                    subject_id=uuid4(),
                    rule_set_version=_RULE_SET,
                    status=status,
                    score=score,
                    finding_count=0,
                    error_count=0,
                    warn_count=0,
                    category_summary={},
                    started_at=now - timedelta(minutes=5),
                    finished_at=now,
                    created_by=ids.editor_a,
                )
            )

        for status, attempts, available_at in (
            ("PENDING", 0, now - timedelta(seconds=120)),
            ("PENDING", 1, now - timedelta(seconds=30)),
            # 尚未到期：不计入积压。
            ("PENDING", 1, now + timedelta(minutes=10)),
            ("FAILED", 3, now),
        ):
            session.add(
                OutboxEvent(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="RAW_ASSET",
                    aggregate_id=uuid4(),
                    event_type="metrics.fixture",
                    payload={},
                    status=status,
                    dispatch=True,
                    attempts=attempts,
                    available_at=available_at,
                    created_at=now,
                )
            )

        # RawAsset 初始状态被数据库触发器锁死为 VALIDATING；先插入再按合法迁移
        # 改为 QUARANTINED，与真实校验链路一致。
        assets = []
        for index, codes in enumerate((["object_missing"], ["malware_suspected"])):
            asset_id = uuid4()
            assets.append((asset_id, codes))
            session.add(
                RawAssetModel(
                    id=asset_id,
                    project_id=ids.project_a,
                    code=f"RAW-{asset_id.hex[:20]}",
                    source_type="UPLOAD",
                    original_filename="fixture.mcap",
                    object_key=f"raw/{ids.project_a}/{asset_id}/content.mcap",
                    size_bytes=16 + index,
                    content_type="application/x-mcap",
                    format="MCAP",
                    sha256=f"sha256:{index:064x}",
                    status="VALIDATING",
                    ingested_at=now,
                    storage_class="STANDARD",
                    uploaded_by=ids.editor_a,
                    created_by=ids.editor_a,
                    updated_by=ids.editor_a,
                )
            )
        session.flush()
        for asset_id, codes in assets:
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="QUARANTINED", quarantine_codes=codes)
            )
        session.commit()
    return last_success


def test_business_metrics_exist_and_match_database(
    metrics_app: tuple[TestClient, ApiIds],
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    client, ids = metrics_app
    factory, _ = api_db
    last_success = _seed_business_rows(factory, ids)

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    # 导入：状态分布 + 最近成功时间 + 24h 吞吐（口径：只算 SUCCEEDED）。
    assert _status(body, "ddp_data_source_imports", "SUCCEEDED") == 2.0
    assert _status(body, "ddp_data_source_imports", "FAILED") == 1.0
    assert _status(body, "ddp_data_source_imports", "PENDING") == 1.0
    # 没有该状态的任务时补 0，仪表盘不断线。
    assert _status(body, "ddp_data_source_imports", "RUNNING") == 0.0
    last_success_value = _sample(body, "ddp_data_source_import_last_success_timestamp_seconds ")
    assert last_success_value is not None
    assert last_success_value == pytest.approx(last_success.timestamp(), abs=1)
    assert _sample(body, "ddp_data_source_imports_succeeded_24h ") == 1.0

    # 处理运行：按状态计数。
    assert _status(body, "ddp_processing_runs", "CREATED") == 1.0
    assert _status(body, "ddp_processing_runs", "QUEUED") == 1.0
    assert _status(body, "ddp_processing_runs", "RUNNING") == 0.0

    # 质量运行：FAILED / INCOMPLETE 可直接用于失败与未完成告警。
    assert _status(body, "ddp_quality_runs", "PASSED") == 1.0
    assert _status(body, "ddp_quality_runs", "FAILED") == 1.0
    assert _status(body, "ddp_quality_runs", "INCOMPLETE") == 1.0

    # outbox：只有到期未投递的 PENDING 计入积压；未来事件不计。
    assert _sample(body, "ddp_outbox_due_events ") == 2.0
    oldest_age = _sample(body, "ddp_outbox_oldest_due_age_seconds ")
    assert oldest_age is not None and oldest_age >= 119.0
    assert _sample(body, "ddp_outbox_failed_events ") == 1.0

    # 存储缺失：只有含 object_missing 的隔离资产计入。
    assert _sample(body, "ddp_raw_assets_object_missing ") == 1.0

    # 指标不得泄露项目/用户标识（服务端全局聚合，无项目标签）。
    assert str(ids.project_a) not in body
    assert str(ids.editor_a) not in body


def test_query_failure_degrades_only_affected_metric_group(
    metrics_app: tuple[TestClient, ApiIds],
    api_db: tuple[sessionmaker[Session], ApiIds],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """某组查询失败：该组不输出样本，其他组照常，/metrics 仍是 200。"""
    client, ids = metrics_app
    factory, _ = api_db
    _seed_business_rows(factory, ids)

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("模拟指标查询失败")

    monkeypatch.setattr(observability, "_distribution", boom)
    caplog.set_level(logging.WARNING, logger="ddp.metrics")

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    # 受影响的组（处理运行走了 _distribution）整体缺席，不输出假 0。
    assert "# TYPE ddp_processing_runs gauge" not in body
    assert 'ddp_processing_runs{status="CREATED"}' not in body
    # 未受影响的组仍按真实数据输出（导入走独立查询，outbox 是标量）。
    assert _status(body, "ddp_data_source_imports", "SUCCEEDED") == 2.0
    assert _sample(body, "ddp_outbox_due_events ") == 2.0
    assert "业务指标 ddp_processing_runs 采集失败" in caplog.text
