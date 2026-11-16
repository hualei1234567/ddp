"""数据源来源判定与投影边端点的领域规则测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import DomainValidationError, LineageConflictError
from app.domain.episodes.lineage import LineageEdge
from app.domain.episodes.provenance import RawAssetSourceStatus, classify_raw_asset_source

_NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("source_type", "linked", "expected"),
    [
        ("IMPORT", True, RawAssetSourceStatus.RESOLVED),
        ("UPLOAD", False, RawAssetSourceStatus.NO_EXTERNAL_SOURCE),
        ("DEVICE", False, RawAssetSourceStatus.NO_EXTERNAL_SOURCE),
        # 声称来自导入却没有媒体行：数据不一致，不能当成"没有来源"。
        ("IMPORT", False, RawAssetSourceStatus.SOURCE_LINK_MISSING),
        # 本项目查不到该资产：未知，同样不能当成"没有来源"。
        (None, False, RawAssetSourceStatus.UNKNOWN_ASSET),
    ],
)
def test_classify_raw_asset_source(
    source_type: str | None,
    linked: bool,
    expected: RawAssetSourceStatus,
) -> None:
    assert classify_raw_asset_source(source_type=source_type, linked=linked) is expected


def test_classify_rejects_unknown_source_type() -> None:
    """未知来源类型必须报错，不能默认成任意一个"看起来正常"的状态。"""
    with pytest.raises(DomainValidationError):
        classify_raw_asset_source(source_type="CLONED", linked=False)


def _edge(
    from_type: LineageNodeType,
    to_type: LineageNodeType,
) -> LineageEdge:
    return LineageEdge(
        edge_id=uuid4(),
        project_id=uuid4(),
        from_node_type=from_type,
        from_node_id=uuid4(),
        to_node_type=to_type,
        to_node_id=uuid4(),
        relation=LineageRelation.PRODUCED,
        created_by=uuid4(),
        created_at=_NOW,
    )


def test_produced_allows_data_source_chain_endpoints() -> None:
    _edge(LineageNodeType.DATA_SOURCE, LineageNodeType.DATA_SOURCE_IMPORT)
    _edge(LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.RAW_ASSET)
    _edge(LineageNodeType.INGESTION_JOB, LineageNodeType.EPISODE)


@pytest.mark.parametrize(
    ("from_type", "to_type"),
    [
        (LineageNodeType.RAW_ASSET, LineageNodeType.EPISODE),
        (LineageNodeType.DATA_SOURCE, LineageNodeType.RAW_ASSET),
        (LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.EPISODE),
    ],
)
def test_produced_still_rejects_unrelated_endpoints(
    from_type: LineageNodeType,
    to_type: LineageNodeType,
) -> None:
    """放宽 PRODUCED 不能变成"任意类型都能连"：方向或组合不对仍然拒绝。"""
    with pytest.raises(LineageConflictError):
        _edge(from_type, to_type)
