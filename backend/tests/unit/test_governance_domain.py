"""治理域单元测试：映射完整性、实体校验与三态对照规则。

这些用例不碰数据库与 HTTP，只钉死领域不变量——它们是报表与配额判定
共同的地基，任何一条被放松都会让"测不到"重新变成"0"。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.governance import (
    DATASET_VERSION_CREATE,
    EPISODE_CREATE,
    INFERENCE_TASK_CREATE,
    INGESTION_JOB_CREATE,
    OBJECT_STORAGE_UPLOAD,
    OPERATION_RESOURCE_KIND,
    DomainValidationError,
    QuotaComparison,
    QuotaStatus,
    ResourceKind,
    ResourceQuota,
    ResourceUsage,
    UsageSnapshot,
    resource_kind_for,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _quota(kind: ResourceKind, limit: int, *, version: int = 1) -> ResourceQuota:
    return ResourceQuota(
        quota_id=uuid4(),
        project_id=uuid4(),
        resource_kind=kind,
        limit_value=limit,
        version=version,
        created_at=NOW,
        updated_at=NOW,
        created_by=uuid4(),
        updated_by=uuid4(),
    )


def _usage(kind: ResourceKind, value: int | None) -> ResourceUsage:
    if value is None:
        return ResourceUsage(resource_kind=kind, value=None)
    return ResourceUsage(
        resource_kind=kind,
        value=value,
        measured_at=NOW,
        source="test.count",
    )


# --- 写操作 → 资源类型映射 -------------------------------------------------
def test_every_resource_kind_has_at_least_one_governed_operation() -> None:
    """每种可计量资源都必须能通过某个写操作被约束，否则上限就是摆设。"""
    assert set(OPERATION_RESOURCE_KIND.values()) == set(ResourceKind)


@pytest.mark.parametrize(
    ("operation", "kind"),
    [
        (EPISODE_CREATE, ResourceKind.EPISODES),
        (DATASET_VERSION_CREATE, ResourceKind.DATASET_VERSIONS),
        (INGESTION_JOB_CREATE, ResourceKind.INGESTION_JOBS),
        (INFERENCE_TASK_CREATE, ResourceKind.INFERENCE_TASKS),
        (OBJECT_STORAGE_UPLOAD, ResourceKind.OBJECT_STORAGE_BYTES),
    ],
)
def test_operation_maps_to_expected_resource(operation: str, kind: ResourceKind) -> None:
    assert resource_kind_for(operation) is kind


def test_unknown_operation_is_rejected_not_defaulted() -> None:
    with pytest.raises(DomainValidationError):
        resource_kind_for("episode.unknown")


# --- 实体校验 --------------------------------------------------------------
def test_quota_rejects_negative_limit_and_bad_kind() -> None:
    with pytest.raises(DomainValidationError):
        _quota(ResourceKind.EPISODES, -1)
    with pytest.raises(DomainValidationError):
        ResourceQuota(
            quota_id=uuid4(),
            project_id=uuid4(),
            resource_kind="NOT_A_KIND",
            limit_value=1,
        )


def test_quota_allows_zero_limit() -> None:
    """0 是"禁止新增"的明确配置，不是无效值。"""
    quota = _quota(ResourceKind.EPISODES, 0)
    assert quota.limit_value == 0


def test_usage_requires_provenance_when_measured() -> None:
    with pytest.raises(DomainValidationError):
        ResourceUsage(resource_kind=ResourceKind.EPISODES, value=1)
    with pytest.raises(DomainValidationError):
        ResourceUsage(resource_kind=ResourceKind.EPISODES, value=1, measured_at=NOW)
    with pytest.raises(DomainValidationError):
        ResourceUsage(
            resource_kind=ResourceKind.EPISODES,
            value=1,
            measured_at=NOW,
            source="",
        )


def test_usage_unmeasured_must_not_carry_source_or_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        ResourceUsage(
            resource_kind=ResourceKind.EPISODES,
            value=None,
            measured_at=NOW,
            source="test.count",
        )


def test_usage_rejects_naive_datetime_and_negative_value() -> None:
    with pytest.raises(DomainValidationError):
        ResourceUsage(
            resource_kind=ResourceKind.EPISODES,
            value=1,
            measured_at=datetime(2026, 11, 17, 12, 0),
            source="test.count",
        )
    with pytest.raises(DomainValidationError):
        ResourceUsage(
            resource_kind=ResourceKind.EPISODES,
            value=-1,
            measured_at=NOW,
            source="test.count",
        )


def test_snapshot_rejects_unmeasured_usage() -> None:
    with pytest.raises(DomainValidationError):
        UsageSnapshot(
            project_id=uuid4(),
            usage=ResourceUsage(resource_kind=ResourceKind.EPISODES, value=None),
        )


# --- 对照状态 --------------------------------------------------------------
def test_comparison_unmeasured_is_not_treated_as_zero() -> None:
    entry = QuotaComparison(
        usage=_usage(ResourceKind.EPISODES, None),
        quota=_quota(ResourceKind.EPISODES, 10),
    )
    assert entry.used is None
    assert entry.remaining is None
    assert entry.exceeded is None
    assert entry.status is QuotaStatus.UNMEASURED


def test_comparison_without_quota_is_unlimited() -> None:
    entry = QuotaComparison(
        usage=_usage(ResourceKind.EPISODES, 7),
        quota=None,
    )
    assert entry.limit is None
    assert entry.remaining is None
    assert entry.exceeded is False
    assert entry.status is QuotaStatus.UNLIMITED


@pytest.mark.parametrize(
    ("used", "limit", "status", "remaining", "exceeded"),
    [
        (3, 10, QuotaStatus.OK, 7, False),
        (10, 10, QuotaStatus.AT_LIMIT, 0, False),
        (11, 10, QuotaStatus.OVER_LIMIT, 0, True),
        (0, 0, QuotaStatus.AT_LIMIT, 0, False),
    ],
)
def test_comparison_boundaries(
    used: int,
    limit: int,
    status: QuotaStatus,
    remaining: int,
    exceeded: bool,
) -> None:
    entry = QuotaComparison(
        usage=_usage(ResourceKind.EPISODES, used),
        quota=_quota(ResourceKind.EPISODES, limit),
    )
    assert entry.status is status
    assert entry.remaining == remaining
    assert entry.exceeded is exceeded


def test_comparison_rejects_mismatched_kinds() -> None:
    with pytest.raises(DomainValidationError):
        QuotaComparison(
            usage=_usage(ResourceKind.EPISODES, 1),
            quota=_quota(ResourceKind.DATASET_VERSIONS, 5),
        )
