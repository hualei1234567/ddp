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


@pytest.mark.parametrize(
    ("from_type", "to_type", "relation"),
    [
        # 标注通过：Episode 被 ANNOTATION 标注。
        (LineageNodeType.EPISODE, LineageNodeType.ANNOTATION, LineageRelation.ANNOTATED_BY),
        # 质量运行：Episode 被 QUALITY_RUN 评估。
        (LineageNodeType.EPISODE, LineageNodeType.QUALITY_RUN, LineageRelation.EVALUATED_BY),
    ],
)
def test_annotation_and_quality_edges_allow_expected_endpoints(
    from_type: LineageNodeType,
    to_type: LineageNodeType,
    relation: LineageRelation,
) -> None:
    edge = LineageEdge(
        edge_id=uuid4(),
        project_id=uuid4(),
        from_node_type=from_type,
        from_node_id=uuid4(),
        to_node_type=to_type,
        to_node_id=uuid4(),
        relation=relation,
        created_by=uuid4(),
        created_at=_NOW,
    )
    assert edge.relation is relation


@pytest.mark.parametrize(
    ("from_type", "to_type", "relation"),
    [
        # 方向必须是从 Episode 出发；反向不是"同一个关系"。
        (LineageNodeType.ANNOTATION, LineageNodeType.EPISODE, LineageRelation.ANNOTATED_BY),
        (LineageNodeType.QUALITY_RUN, LineageNodeType.EPISODE, LineageRelation.EVALUATED_BY),
        # 交叉类型不能借用这两个关系。
        (LineageNodeType.EPISODE, LineageNodeType.QUALITY_RUN, LineageRelation.ANNOTATED_BY),
    ],
)
def test_annotation_and_quality_edges_reject_wrong_endpoints(
    from_type: LineageNodeType,
    to_type: LineageNodeType,
    relation: LineageRelation,
) -> None:
    with pytest.raises(LineageConflictError):
        LineageEdge(
            edge_id=uuid4(),
            project_id=uuid4(),
            from_node_type=from_type,
            from_node_id=uuid4(),
            to_node_type=to_type,
            to_node_id=uuid4(),
            relation=relation,
            created_by=uuid4(),
            created_at=_NOW,
        )


def test_trained_on_allows_dataset_version_to_training_run() -> None:
    """``DatasetVersion --TRAINED_ON--> TrainingRun`` 是 ADR-0036 冻结的方向。"""
    edge = LineageEdge(
        edge_id=uuid4(),
        project_id=uuid4(),
        from_node_type=LineageNodeType.DATASET_VERSION,
        from_node_id=uuid4(),
        to_node_type=LineageNodeType.TRAINING_RUN,
        to_node_id=uuid4(),
        relation=LineageRelation.TRAINED_ON,
        created_by=uuid4(),
        created_at=_NOW,
    )
    assert edge.relation is LineageRelation.TRAINED_ON
    assert LineageRelation.TRAINED_ON.value == "TRAINED_ON"


@pytest.mark.parametrize(
    ("from_type", "to_type"),
    [
        # 方向不能反：训练运行是消费方，不是数据集版本的上游。
        (LineageNodeType.TRAINING_RUN, LineageNodeType.DATASET_VERSION),
        # 不能借用该关系连接其他节点对。
        (LineageNodeType.DATASET_VERSION, LineageNodeType.EPISODE),
        (LineageNodeType.EPISODE, LineageNodeType.TRAINING_RUN),
        (LineageNodeType.DATASET_VERSION, LineageNodeType.DATASET_VERSION),
    ],
)
def test_trained_on_rejects_reverse_or_unrelated_endpoints(
    from_type: LineageNodeType,
    to_type: LineageNodeType,
) -> None:
    with pytest.raises(LineageConflictError):
        LineageEdge(
            edge_id=uuid4(),
            project_id=uuid4(),
            from_node_type=from_type,
            from_node_id=uuid4(),
            to_node_type=to_type,
            to_node_id=uuid4(),
            relation=LineageRelation.TRAINED_ON,
            created_by=uuid4(),
            created_at=_NOW,
        )
