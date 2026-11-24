"""保留策略用例（V51-017）：影响预览 + 事务级阻断。

## 这个模块负责什么

1. **只读预览**：删除/销毁前查询"谁在引用它"。不落库、不改状态。
2. **事务级阻断**：在**用例层**（而非 API 层、更不是前端按钮）判定是否放行，
   并返回稳定错误码 + 引用证据。

## 为什么阻断必须在用例层

蓝图 §20.4 明确要求"引用保护优先使用约束/事务级校验，不依赖前端按钮"。
前端隐藏按钮只能防误点，防不住脚本、Adapter、运维直接调用。因此判定放在
领域状态变更的同一事务里，且**在持锁下**重新读取引用——查完到写完之间的
窗口必须闭合（见 ports 的并发协议长文档）。

## 模块边界

本模块不直接写 ``raw_assets`` 状态：它返回裁决（放行/拒绝），由
``RawAssetUseCases.apply_action`` 在既有事务里继续原有写入路径。这样
审计、outbox、幂等、乐观锁都不需要复制一份。
"""

from __future__ import annotations

from uuid import UUID

from app.application.retention.ports import ReferenceImpactRepository
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.errors import (
    DomainValidationError,
    RawAssetReferencedByFrozenDatasetError,
    ResourceNotFoundError,
)
from app.domain.retention.models import ReferenceImpact
from app.domain.retention.policy import is_content_discarding, retention_verdict


class ReferenceImpactUseCases:
    """引用影响查询与销毁阻断。

    ``repository`` 必须实现 :class:`ReferenceImpactRepository`。
    """

    def __init__(self, references: ReferenceImpactRepository) -> None:
        self._references = references

    # ------------------------------------------------------------------ #
    # 只读预览
    # ------------------------------------------------------------------ #

    def preview_raw_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> ReferenceImpact:
        """RawAsset 的冻结引用影响（不落库、不改状态）。

        资产不存在或跨项目统一走 ``ResourceNotFoundError``：不把"别人的资产"
        与"不存在的资产"区分开，避免变成存在性探针。

        存在性必须**先**校验：只查引用会返回"0 个引用、可以删"，把一个
        不存在的 ID 渲染成"安全可删"的假结论。
        """
        if not self._references.raw_asset_exists(scope, asset_id):
            raise ResourceNotFoundError("原始资产", asset_id)
        return self._references.raw_asset_impact(scope, asset_id)

    def preview_processing_artifact(
        self,
        scope: ProjectScope,
        run_id: UUID,
        object_key: str,
    ) -> ReferenceImpact:
        """ProcessingArtifact 的派生引用影响（预览用，不阻断）。

        ``object_key`` 长度上限与 ``processing_artifacts.object_key`` 列一致，
        超长直接拒绝而不是截断——截断会让预览查错对象。
        """
        if not object_key or len(object_key) > 1024:
            raise DomainValidationError("object_key 长度必须在 1 到 1024 之间")
        return self._references.processing_artifact_impact(scope, run_id, object_key)

    # ------------------------------------------------------------------ #
    # 事务内阻断（核心红线）
    # ------------------------------------------------------------------ #

    def assert_destroy_allowed(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        *,
        action: str,
    ) -> None:
        """销毁动作准入：锁资产行 + 持锁重算引用 + 有冻结引用则拒绝。

        **必须在动作自身的写事务内调用**（调用方不应在外层另起事务提交），
        否则锁在调用返回时就释放，窗口重新打开。

        非销毁动作（``archive`` 等）直接返回，不付锁与查询的代价——
        归档不丢内容，没有阻断的必要。
        """
        if not is_content_discarding(action):
            return
        # 1) 先锁：把并发冻结串行化到本事务之后。
        status = self._references.lock_raw_asset(scope, asset_id)
        if status is None:
            raise ResourceNotFoundError("原始资产", asset_id)
        # 2) 持锁后重新计算：此时读到的是"已提交 + 已获锁"的最新引用集合。
        impact = self._references.raw_asset_impact(scope, asset_id)
        verdict = retention_verdict(impact, action=action)
        if verdict.allowed:
            return
        raise RawAssetReferencedByFrozenDatasetError(
            verdict.reason or "被冻结数据集版本引用，禁止销毁内容",
            details=_impact_details(impact),
        )

    def assert_freeze_allowed(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
        *,
        asset_ids: tuple[UUID, ...] | None = None,
    ) -> None:
        """冻结准入：锁本版本引用到的资产行 + 其中有已销毁内容则拒绝冻结。

        这是并发协议的**另一侧**：销毁方锁单个资产行，冻结方锁同一批行，
        两者必然冲突。因此不可能出现"一边判定无引用、另一边同时冻结成功"。

        ``asset_ids`` 可显式传入以免重复查询；缺省时从仓储按版本反查。

        拒绝条件是"引用了已丢弃内容的资产"——这对应蓝图 §5 完整性检查里
        "Episode 没有 RawAsset 或合法来源：阻断冻结"。``TOMBSTONED`` /
        ``REJECTED`` 的资产不再提供可复现内容，因此冻结必须失败。
        """
        resolved = (
            asset_ids
            if asset_ids is not None
            else self._references.raw_asset_ids_for_version(scope, dataset_version_id)
        )
        if not resolved:
            # 版本没有引用任何原始资产：冻结侧没有可锁对象，也没有可破坏的
            # 引用。完整性检查（缺 Episode）由冻结用例自身的既有校验负责。
            return
        locked = self._references.lock_raw_assets(scope, tuple(resolved))
        discarded = sorted(
            asset_id for asset_id, status in locked.items() if status in _CONTENT_DISCARDED_STATUSES
        )
        if discarded:
            raise RawAssetReferencedByFrozenDatasetError(
                f"数据集版本引用了 {len(discarded)} 个已丢弃内容的原始资产，禁止冻结",
                details={
                    "reason": "raw_asset_content_discarded",
                    "discarded_asset_ids": [str(item) for item in discarded],
                    "discarded_asset_count": len(discarded),
                },
            )


#: 内容已被丢弃的资产状态：下载被拒且不再计入存储占用。冻结时引用它们
#: 会让版本永久不可复现，必须阻断。
_CONTENT_DISCARDED_STATUSES: frozenset[str] = frozenset({"TOMBSTONED", "REJECTED"})


def _impact_details(impact: ReferenceImpact) -> dict[str, object]:
    """拒绝时回显的引用证据。

    只含请求方本就能读到的对象标识与该版本自身的事实（版本号、状态、
    Episode 数），不含跨项目信息、不含任何内容摘要。用于让前端能直接渲染
    "会被毁掉的 3 个冻结版本"，而不必再发一轮请求。
    """
    return {
        "reason": "referenced_by_frozen_dataset",
        "target_kind": impact.target_kind.value,
        "target_id": str(impact.target_id),
        "dataset_version_count": impact.dataset_version_count,
        "episode_count": impact.episode_count,
        "frozen_versions": [
            {
                "dataset_version_id": str(item.dataset_version_id),
                "dataset_id": str(item.dataset_id),
                "dataset_code": item.dataset_code,
                "dataset_version": item.dataset_version,
                "status": item.status,
                "frozen_at": item.frozen_at.isoformat(),
                "episode_count": item.episode_count,
            }
            for item in impact.references
        ],
        "allowed_alternatives": ["archive"],
    }


__all__ = ["ReferenceImpactUseCases"]
