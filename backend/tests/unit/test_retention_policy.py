"""V51-017 保留策略：动作分类与裁决的纯领域单测。

覆盖三件事，且**不碰数据库**（策略是纯函数，可以穷举组合）：

1. 哪些动作算"销毁内容"——``tombstone`` / ``reject-quarantined`` 算，
   ``archive`` / 隔离类不算。归档被误判为销毁会让唯一的合规退场路径失效，
   这是本任务最容易写错、后果最严重的一处，因此逐个动作断言。
2. 有冻结引用 + 销毁动作 → 拒绝，且携带引用证据与稳定错误码。
3. 只读预览（``action=None``）永不阻断；无引用时任何动作都放行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.retention.models import (
    FrozenDatasetReference,
    ReferenceImpact,
    ReferenceTargetKind,
)
from app.domain.retention.policy import (
    CONTENT_DISCARDING_ACTIONS,
    CONTENT_PRESERVING_ACTIONS,
    RAW_ASSET_REFERENCED_BY_FROZEN_DATASET,
    is_content_discarding,
    retention_verdict,
)

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _reference(
    *,
    dataset_version: int = 1,
    episode_count: int = 3,
    status: str = "FROZEN",
) -> FrozenDatasetReference:
    return FrozenDatasetReference(
        dataset_version_id=uuid4(),
        dataset_id=uuid4(),
        dataset_version=dataset_version,
        dataset_code=f"ds-{dataset_version}",
        status=status,
        frozen=True,
        frozen_at=NOW,
        episode_count=episode_count,
    )


def _impact(*references: FrozenDatasetReference) -> ReferenceImpact:
    return ReferenceImpact(
        target_kind=ReferenceTargetKind.RAW_ASSET,
        target_id=uuid4(),
        references=tuple(references),
    )


# --------------------------------------------------------------------------- #
# 动作分类
# --------------------------------------------------------------------------- #


def test_tombstone_is_content_discarding() -> None:
    """墓碑丢弃内容：状态 TOMBSTONED 后下载被拒、不再计入存储。"""
    assert is_content_discarding("tombstone") is True


def test_reject_quarantined_is_content_discarding() -> None:
    """驳回隔离同样丢弃内容。

    客观效果与墓碑相同（REJECTED 与 TOMBSTONED 在存储计量里同属"已丢弃"、
    下载同样被拒）。若这里判 False，"先隔离再驳回"就成了绕过保护的后门。
    """
    assert is_content_discarding("reject-quarantined") is True


@pytest.mark.parametrize(
    "action",
    ["archive", "post-hoc-quarantine", "release-quarantine-or-revalidate"],
)
def test_archive_and_quarantine_do_not_discard_content(action: str) -> None:
    """归档与隔离不丢内容，必须永不被引用保护阻断。

    归档是蓝图明确要求的合规退场路径；把归档也阻断会让被引用的资产
    永久无法退场（既不能删也不能归档），与设计冲突。
    """
    assert is_content_discarding(action) is False


def test_discarding_and_preserving_action_sets_are_disjoint() -> None:
    """两组动作不得重叠：一个动作不可能既丢内容又不丢内容。"""
    assert not (CONTENT_DISCARDING_ACTIONS & CONTENT_PRESERVING_ACTIONS)


def test_unknown_action_is_treated_as_non_discarding() -> None:
    """未知动作当作不销毁。

    动作白名单由领域状态机先行校验，走到策略时动作已合法；对未知值"宽松"
    是安全的，因为真正的拦截点仍是状态机。若这里抛错，会让非销毁路径
    因为一个无关分支而失败。
    """
    assert is_content_discarding("no-such-action") is False


# --------------------------------------------------------------------------- #
# 裁决：阻断
# --------------------------------------------------------------------------- #


def test_frozen_reference_blocks_tombstone() -> None:
    verdict = retention_verdict(_impact(_reference()), action="tombstone")
    assert verdict.allowed is False
    assert verdict.code == RAW_ASSET_REFERENCED_BY_FROZEN_DATASET
    assert verdict.reason is not None
    assert "归档" in verdict.reason


def test_frozen_reference_blocks_reject_quarantined() -> None:
    """驳回隔离同样被阻断，否则是绕过墓碑保护的后门。"""
    verdict = retention_verdict(_impact(_reference()), action="reject-quarantined")
    assert verdict.allowed is False
    assert verdict.code == RAW_ASSET_REFERENCED_BY_FROZEN_DATASET


def test_multiple_references_are_all_reported() -> None:
    """拒绝时必须给出**完整**引用集合，而不是第一个。

    UI 需要展示"会毁掉哪几个版本"，只报一个会让用户以为影响面更小。
    """
    impact = _impact(
        _reference(dataset_version=1, episode_count=2),
        _reference(dataset_version=2, episode_count=5),
    )
    verdict = retention_verdict(impact, action="tombstone")
    assert verdict.allowed is False
    assert len(verdict.impact.references) == 2
    assert verdict.impact.dataset_version_count == 2
    assert verdict.impact.episode_count == 7


def test_superseded_and_archived_frozen_versions_still_block() -> None:
    """被取代/已归档的**冻结过**版本仍受保护。

    这是本任务最关键的边界：若按 ``status == 'FROZEN'`` 判定，
    ``SUPERSEDED`` / ``ARCHIVED`` 下保护会静默消失。它们的 ``frozen_at``
    仍在，引用的内容同样是已冻结版本的可复现证据。
    """
    for status in ("SUPERSEDED", "ARCHIVED", "FROZEN"):
        verdict = retention_verdict(_impact(_reference(status=status)), action="tombstone")
        assert verdict.allowed is False, status


# --------------------------------------------------------------------------- #
# 裁决：放行
# --------------------------------------------------------------------------- #


def test_no_reference_allows_destruction() -> None:
    """没有冻结引用时销毁是安全的（普通未使用资产要能被清理）。"""
    verdict = retention_verdict(_impact(), action="tombstone")
    assert verdict.allowed is True
    assert verdict.code is None


def test_archive_is_allowed_even_with_frozen_references() -> None:
    """归档永不被阻断——它是蓝图指定的合规退场路径。"""
    verdict = retention_verdict(_impact(_reference()), action="archive")
    assert verdict.allowed is True
    assert verdict.code is None


def test_read_only_preview_never_blocks() -> None:
    """``action=None`` 表示纯预览：如实报告影响，不替用户决定。

    若预览也返回 allowed=False，调用方会把"查影响"误当成"已经被拒绝"。
    """
    verdict = retention_verdict(_impact(_reference()), action=None)
    assert verdict.allowed is True
    assert verdict.code is None
    assert verdict.impact.protected is True
