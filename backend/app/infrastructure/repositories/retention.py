"""保留策略（V51-017）SQLAlchemy 仓储：冻结引用影响与锁协议。

## 查询路径与索引依据

RawAsset 影响查询是一条 4 表 join，每一跳都已有索引（本次迁移**未新增**
任何索引，已核对 ``pg_indexes``）：

1. ``dataset_versions`` — ``ix_dataset_versions_project_dataset_status``
   不足以覆盖 ``frozen_at IS NOT NULL``，但该表行数小（每项目每数据集版本数），
   且过滤条件本身高度选择；真实规模下由 ``project_id`` 前缀先裁剪即可。
2. ``dataset_items`` — ``ix_dataset_items_project_version_split`` 的
   ``(project_id, dataset_version_id)`` 前缀正好命中。
3. ``episodes`` — join 走主键 ``(project_id, id)``。
4. ``episode_raw_assets`` — ``ix_episode_raw_assets_project_asset``
   ``(project_id, asset_id)`` **正是本查询的过滤方向**（从资产反查 Episode）。

## 为什么把 project_id 写进每个 JOIN 的 ON 子句

表间用 ``(project_id, id)`` 复合外键互指，但 SQL 的 join 条件若不显式带上
``project_id``，跨项目行会按 id 误配。这里所有 join 都带 project 等值条件，
并且 WHERE 也带，因此跨项目数据既不计入影响也不泄露存在性——与仓储层
"强制 project 作用域"的既有约定一致。

## 锁协议

见 :mod:`app.application.retention.ports` 的长文档。要点：三方都锁
``raw_assets`` 行；批量加锁按 UUID 升序以固定顺序。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.domain.datasets.enums import DatasetVersionStatus
from app.domain.identity.models import ProjectScope
from app.domain.retention.models import (
    FrozenDatasetReference,
    ReferenceImpact,
    ReferenceTargetKind,
)
from app.infrastructure.db.models.datasets import (
    DatasetItemModel,
    DatasetModel,
    DatasetVersionModel,
)
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.processing import ProcessingArtifactModel
from app.infrastructure.repositories._dataset_sql import as_utc

#: 「曾经冻结」的判定：``frozen_at`` 一旦写入即被数据集域触发器列为不可变。
#: 不按 ``status = 'FROZEN'`` 判定——``SUPERSEDED`` / ``ARCHIVED`` 同样是
#: 冻结过的版本，其引用必须继续受保护（详见 domain/retention/models.py）。
_FROZEN_AT_PRESENT = DatasetVersionModel.frozen_at.is_not(None)

#: 归档不算销毁内容，但归档版本依然受引用保护——这里不区分，全部计入。
_SUPERSEDED_LIKE = (
    DatasetVersionStatus.FROZEN.value,
    DatasetVersionStatus.SUPERSEDED.value,
    DatasetVersionStatus.ARCHIVED.value,
)


class SqlReferenceImpactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # 只读：影响预览
    # ------------------------------------------------------------------ #

    def raw_asset_impact(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> ReferenceImpact:
        """资产 → Episode → DatasetItem → 曾冻结的 DatasetVersion。

        一次查询完成，不做逐版本 N+1。按 ``(dataset_version_id)`` 分组计数
        Episode 去重数；版本元信息在同一行里带出。
        """
        statement = (
            select(
                DatasetVersionModel.id,
                DatasetVersionModel.dataset_id,
                DatasetVersionModel.dataset_version,
                DatasetVersionModel.status,
                DatasetVersionModel.frozen_at,
                DatasetModel.code,
                func.count(func.distinct(EpisodeRawAssetModel.episode_id)).label("episode_count"),
            )
            .select_from(EpisodeRawAssetModel)
            .join(
                EpisodeModel,
                and_(
                    EpisodeModel.project_id == EpisodeRawAssetModel.project_id,
                    EpisodeModel.id == EpisodeRawAssetModel.episode_id,
                ),
            )
            .join(
                DatasetItemModel,
                and_(
                    DatasetItemModel.project_id == EpisodeModel.project_id,
                    DatasetItemModel.episode_id == EpisodeModel.id,
                ),
            )
            .join(
                DatasetVersionModel,
                and_(
                    DatasetVersionModel.project_id == DatasetItemModel.project_id,
                    DatasetVersionModel.id == DatasetItemModel.dataset_version_id,
                ),
            )
            .outerjoin(
                DatasetModel,
                and_(
                    DatasetModel.project_id == DatasetVersionModel.project_id,
                    DatasetModel.id == DatasetVersionModel.dataset_id,
                ),
            )
            .where(
                EpisodeRawAssetModel.project_id == scope.project_id,
                EpisodeRawAssetModel.asset_id == asset_id,
                DatasetVersionModel.project_id == scope.project_id,
                _FROZEN_AT_PRESENT,
                # status 过滤是**冗余但必要**的下推：frozen_at 不可变，所以
                # 只要它非空，status 必在三个值之一。这里显式列出是为了让
                # 优化器能用上 (project_id, dataset_id, status) 索引前缀。
                DatasetVersionModel.status.in_(_SUPERSEDED_LIKE),
            )
            .group_by(
                DatasetVersionModel.id,
                DatasetVersionModel.dataset_id,
                DatasetVersionModel.dataset_version,
                DatasetVersionModel.status,
                DatasetVersionModel.frozen_at,
                DatasetModel.code,
            )
            .order_by(
                DatasetVersionModel.dataset_version.desc(),
                DatasetVersionModel.id,
            )
        )
        rows = self._session.execute(statement).all()
        references = tuple(
            FrozenDatasetReference(
                dataset_version_id=row[0],
                dataset_id=row[1],
                dataset_version=int(row[2]),
                status=str(row[3]),
                frozen_at=as_utc(row[4]),
                dataset_code=row[5],
                frozen=True,
                episode_count=int(row[6] or 0),
            )
            for row in rows
        )
        return ReferenceImpact(
            target_kind=ReferenceTargetKind.RAW_ASSET,
            target_id=asset_id,
            references=references,
        )

    def processing_artifact_impact(
        self,
        scope: ProjectScope,
        run_id: UUID,
        object_key: str,
    ) -> ReferenceImpact:
        """产物 → ``source_ref`` 指向的 RawAsset → 曾冻结版本（**派生**结论）。

        没有直接的 ``DatasetVersion → ProcessingArtifact`` 边（schema 里不存在），
        所以这里只能沿产物行自带的 ``source_ref`` 反解出原始资产，再复用
        RawAsset 的权威查询。解析不出（``source_ref`` 为空或是 Episode
        manifest key）时返回空影响——**如实表示"无法证明"，不猜**。
        """
        source_refs = self._session.scalars(
            select(ProcessingArtifactModel.source_ref)
            .where(
                ProcessingArtifactModel.project_id == scope.project_id,
                ProcessingArtifactModel.run_id == run_id,
                ProcessingArtifactModel.object_key == object_key,
                ProcessingArtifactModel.source_ref.is_not(None),
            )
            .distinct()
        ).all()
        project_text = str(scope.project_id)
        asset_ids: list[UUID] = []
        for ref in source_refs:
            if not isinstance(ref, str):
                continue
            asset_id = _raw_asset_id_from_ref(ref, project_text)
            if asset_id is not None:
                asset_ids.append(asset_id)
        merged: dict[UUID, FrozenDatasetReference] = {}
        for asset_id in sorted(set(asset_ids)):
            impact = self.raw_asset_impact(scope, asset_id)
            for reference in impact.references:
                # 同一版本可能经多条来源路径命中：保留最大 Episode 计数，
                # 而不是相加（同一 Episode 被重复计会虚增影响面）。
                current = merged.get(reference.dataset_version_id)
                if current is None:
                    merged[reference.dataset_version_id] = reference
                elif reference.episode_count > current.episode_count:
                    merged[reference.dataset_version_id] = reference
        ordered = tuple(
            sorted(
                merged.values(),
                key=lambda item: (item.dataset_version, str(item.dataset_version_id)),
                reverse=True,
            )
        )
        return ReferenceImpact(
            target_kind=ReferenceTargetKind.PROCESSING_ARTIFACT,
            target_id=run_id,
            references=ordered,
        )

    # ------------------------------------------------------------------ #
    # 存在性（只读预览用）
    # ------------------------------------------------------------------ #

    def raw_asset_exists(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> bool:
        row = self._session.scalar(
            select(RawAssetModel.id).where(
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.id == asset_id,
            )
        )
        return row is not None

    # ------------------------------------------------------------------ #
    # 事务内：锁 + 状态
    # ------------------------------------------------------------------ #

    def lock_raw_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> str | None:
        row = self._session.execute(
            select(RawAssetModel.status)
            .where(
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.id == asset_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        return None if row is None else str(row)

    def lock_raw_assets(
        self,
        scope: ProjectScope,
        asset_ids: tuple[UUID, ...],
    ) -> dict[UUID, str]:
        if not asset_ids:
            return {}
        # 升序固定加锁顺序：两条路径（销毁方批量、冻结方批量）若顺序不同会
        # 在小概率下互锁成环。UUID 排序是稳定且与业务无关的次序。
        ordered = tuple(sorted(set(asset_ids), key=str))
        rows = self._session.execute(
            select(RawAssetModel.id, RawAssetModel.status)
            .where(
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.id.in_(ordered),
            )
            .order_by(RawAssetModel.id)
            .with_for_update()
        ).all()
        return {row[0]: str(row[1]) for row in rows}

    def raw_asset_ids_for_version(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> tuple[UUID, ...]:
        rows = self._session.scalars(
            select(EpisodeRawAssetModel.asset_id)
            .select_from(DatasetItemModel)
            .join(
                EpisodeModel,
                and_(
                    EpisodeModel.project_id == DatasetItemModel.project_id,
                    EpisodeModel.id == DatasetItemModel.episode_id,
                ),
            )
            .join(
                EpisodeRawAssetModel,
                and_(
                    EpisodeRawAssetModel.project_id == EpisodeModel.project_id,
                    EpisodeRawAssetModel.episode_id == EpisodeModel.id,
                ),
            )
            .where(
                DatasetItemModel.project_id == scope.project_id,
                DatasetItemModel.dataset_version_id == dataset_version_id,
                EpisodeRawAssetModel.project_id == scope.project_id,
            )
            .distinct()
            .order_by(EpisodeRawAssetModel.asset_id)
        ).all()
        return tuple(rows)


def _raw_asset_id_from_ref(ref: str, project_text: str) -> UUID | None:
    """从 ``raw/<project>/<asset_id>/content.<ext>`` 形态的 key 解析资产 ID。

    复用领域权威 Key 规范（``validate_raw_object_key``），不自己写正则；
    项目段必须等于本项目，否则视为"不是我的输入"而不是去查它是否存在。
    """
    from app.domain.ingestion.validation import validate_raw_object_key

    if not validate_raw_object_key(ref):
        return None
    parts = ref.split("/")
    if len(parts) < 6 or parts[1] != project_text:
        return None
    try:
        return UUID(parts[5])
    except ValueError:
        return None


__all__ = ["SqlReferenceImpactRepository"]
