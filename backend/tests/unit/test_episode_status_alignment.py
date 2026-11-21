"""Episode 可训练口径与 Canonical 版本的一致性（ADR-0038）。

两个域各自内联了「可用于训练」的 Episode 状态集合，为避免新增领域间依赖，
它们不 import 同一常量。这带来一个只能靠测试兜住的风险：任何一处被单独修改，
另外两处不会跟着变，门禁就会悄悄分叉。

本文件把 ADR-0038 的两条决策变成可执行约束：

1. `datasets.selection` 与 `annotation.selection` 的状态集合必须与
   `episodes.state_machines.EPISODE_COVERABLE_STATUSES` 完全一致，且都不含
   `ARCHIVED`（归档是退出活跃集，不是质量更好）。
2. 导入物化产出的 `canonical_schema_version` 必须是 `ddp_data.canonical.v1`
   支持的正式版本——否则 V31-017 接上共享 JSON Schema 时历史导入产物会立刻被拒绝。
"""

from __future__ import annotations

from app.domain.annotation import selection as annotation_selection
from app.domain.datasets import selection as dataset_selection
from app.domain.episodes.enums import EpisodeStatus
from app.domain.episodes.state_machines import EPISODE_COVERABLE_STATUSES
from ddp_data.canonical.v1 import SUPPORTED_SCHEMA_VERSIONS


def test_coverable_statuses_are_accepted_and_completed() -> None:
    assert {status.value for status in EPISODE_COVERABLE_STATUSES} == {
        "ACCEPTED",
        "COMPLETED",
    }


def test_dataset_selection_matches_coverable_statuses() -> None:
    assert dataset_selection._EPISODE_STATUSES == {
        status.value for status in EPISODE_COVERABLE_STATUSES
    }


def test_annotation_selection_matches_coverable_statuses() -> None:
    assert annotation_selection._ALLOWED_STATUSES == {
        status.value for status in EPISODE_COVERABLE_STATUSES
    }


def test_archived_episode_is_not_trainable() -> None:
    archived = EpisodeStatus.ARCHIVED.value
    assert archived not in {status.value for status in EPISODE_COVERABLE_STATUSES}
    assert archived not in dataset_selection._EPISODE_STATUSES
    assert archived not in annotation_selection._ALLOWED_STATUSES


def test_import_materialization_uses_supported_canonical_version() -> None:
    from app.application.datasources.episode_materialization import (
        IMPORT_CANONICAL_SCHEMA_VERSION,
    )

    assert IMPORT_CANONICAL_SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS
