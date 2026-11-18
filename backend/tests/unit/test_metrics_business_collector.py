"""业务指标采集器（``app.core.metrics``）的口径与降级语义。

这些用例不查库：只验证"快照 → Prometheus 样本"的映射规则。数据库查询本身
（索引、超时、按组降级）在 ``tests/api/test_metrics_business_api.py`` 用真实
SQLite 覆盖。
"""

from __future__ import annotations

import pytest
from app.core.metrics import (
    EPISODES_GAUGE,
    HTTP_REQUESTS_TOTAL,
    IMPORT_LAST_SUCCESS_GAUGE,
    IMPORTS_GAUGE,
    OUTBOX_DUE_GAUGE,
    OUTBOX_OLDEST_DUE_AGE_GAUGE,
    QUALITY_RUNS_GAUGE,
    BusinessSnapshot,
    build_metrics,
)
from app.domain.datasources.enums import ImportStatus
from prometheus_client import generate_latest


def _render(snapshot: BusinessSnapshot) -> str:
    metrics = build_metrics(snapshot_provider=lambda: snapshot)
    return generate_latest(metrics.registry).decode()


def test_value_families_have_no_labels_and_skip_none() -> None:
    """标量指标只有一条序列；``None``（无样本）不输出，不伪装成 0。"""
    body = _render(
        BusinessSnapshot(
            status_counts={},
            values={
                OUTBOX_DUE_GAUGE: 3,
                OUTBOX_OLDEST_DUE_AGE_GAUGE: 0.0,
                IMPORT_LAST_SUCCESS_GAUGE: None,
            },
        )
    )

    assert "# TYPE ddp_outbox_due_events gauge" in body
    assert "ddp_outbox_due_events 3.0" in body
    assert "ddp_outbox_oldest_due_age_seconds 0.0" in body
    assert IMPORT_LAST_SUCCESS_GAUGE not in body


def test_missing_family_is_absent_but_present_family_zero_fills_statuses() -> None:
    """组内缺状态补 0；整组缺失（采集失败）不输出——两者语义不同。"""
    body = _render(BusinessSnapshot(status_counts={IMPORTS_GAUGE: {}}, values={}))

    for status in ImportStatus:
        assert f'{IMPORTS_GAUGE}{{status="{status.value}"}} 0.0' in body
    # 没有给出的质量运行组不得以全 0 出现。
    assert f"# TYPE {QUALITY_RUNS_GAUGE} gauge" not in body
    assert f"# TYPE {EPISODES_GAUGE} gauge" not in body


def test_snapshot_provider_failure_keeps_http_metrics() -> None:
    """查询整体失败：业务样本整体缺失，/metrics 仍可抓取 HTTP 指标。"""

    def explode() -> BusinessSnapshot:
        raise RuntimeError("数据库不可用")

    metrics = build_metrics(snapshot_provider=explode)
    body = generate_latest(metrics.registry).decode()

    assert f"# TYPE {IMPORTS_GAUGE} gauge" not in body
    assert f"# TYPE {HTTP_REQUESTS_TOTAL} counter" in body


def test_two_providers_are_rejected() -> None:
    """两套事实来源同时给出会互相覆盖，装配期直接失败而不是静默择一。"""
    with pytest.raises(ValueError, match="只能二选一"):
        build_metrics(
            snapshot_provider=lambda: BusinessSnapshot(status_counts={}, values={}),
            counts_provider=lambda: {},
        )
