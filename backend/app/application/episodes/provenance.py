"""数据源链的只读投影：把数据源三张表的既有外键展开成血缘边。

设计取舍（为什么不做成持久化边）：

1. **权威事实只存一处**。``data_sources`` / ``data_source_imports`` /
   ``data_source_media_assets`` 已经用外键表达了「数据源 → 导入任务 →
   媒体资产 → RawAsset」，再往 ``data_lineage_edges`` 写一份必然漂移；
2. **历史数据同等可见**。持久化方案只覆盖上线后新写的边，已有导入（例如
   384 条历史媒体资产）仍然查不到来源，等于同一个问题只修一半；写回填
   迁移又要绕过只追加边的不可变承诺（``data_lineage_edges`` 禁止 UPDATE /
   DELETE，回滚只能临时禁用触发器）；
3. **不伪造真实数据**。投影边的 ``created_by`` / ``created_at`` 取自权威行
   （导入任务或媒体资产行的创建者与创建时间），不是编造的操作者；
   边的 ``metadata`` 里显式标注 ``derived=true``，与持久化边区分。

投影边 id 由端点确定性派生（``uuid5``），同一份数据反复查询得到同一个 id，
既不重复也不随机。

本模块只做「读 + 组装」，不写库；查询在 ``infrastructure`` 的
:class:`app.infrastructure.repositories.datasource_provenance.SqlSourceProvenanceRepository`，
原始资产声明的来源类型走 ingestion 模块自己的仓储端口。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from app.application.ingestion.ports import RawAssetRepository
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.lineage import LineageEdge
from app.domain.episodes.provenance import RawAssetSourceStatus, classify_raw_asset_source
from app.domain.identity.models import ProjectScope

# 确定性边 id 的命名空间：固定常量，改动它等于让所有投影边换 id。
_DERIVED_EDGE_NAMESPACE = UUID("8a6f9f2c-3f39-4f2b-9c86-2d7f1c5a0b51")

# 单次投影查询的行上限：与血缘遍历的 ``limit`` 同口径，避免无界查询。
DEFAULT_PROJECTION_LIMIT = 100


@dataclass(frozen=True, slots=True)
class SourceLink:
    """一条 ``data_source_media_assets`` 行连同数据源与导入任务的属性。

    来自「按数据源/按导入任务」的两个查询时，媒体资产部分可以为 ``None``
    （导入任务尚未产出任何媒体文件，这在 ``PENDING``/``RUNNING``/``FAILED``
    状态下是正常结果，不能被当成"没有导入任务"）。
    """

    project_id: UUID
    data_source_id: UUID
    data_source_code: str
    data_source_name: str
    data_source_kind: str
    repo_id: str
    revision: str
    import_id: UUID
    import_status: str
    import_scope: str
    import_created_by: UUID
    import_created_at: datetime
    import_started_at: datetime | None = None
    import_finished_at: datetime | None = None
    imported_file_count: int | None = None
    media_asset_id: UUID | None = None
    raw_asset_id: UUID | None = None
    episode_key: str | None = None
    source_path: str | None = None
    camera: str | None = None
    media_created_by: UUID | None = None
    media_created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "data_source_id",
            "import_id",
            "import_created_by",
            "media_asset_id",
            "raw_asset_id",
            "media_created_by",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, UUID):
                raise DomainValidationError(f"{name} 必须是 UUID")
        for name in (
            "import_created_at",
            "import_started_at",
            "import_finished_at",
            "media_created_at",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise DomainValidationError(f"{name} 必须是带时区的 datetime")


@dataclass(frozen=True, slots=True)
class RawAssetOrigin:
    """一条 RawAsset 的外部来源结论：状态 + 权威关联行。"""

    raw_asset_id: UUID
    status: RawAssetSourceStatus
    links: tuple[SourceLink, ...] = ()


class SourceProvenanceRepository(Protocol):
    """数据源链只读端口；实现必须硬绑 ``scope.project_id``。

    三个方法返回同一行形状（:class:`SourceLink`），区别只在过滤维度：

    - ``list_links_by_raw_assets``：按 RawAsset 反查（反向血缘）；
    - ``list_links_by_imports``：按导入任务查（导入未产出媒体时为媒体列空值）；
    - ``list_links_by_data_sources``：按数据源查（正向血缘）。
    """

    def list_links_by_raw_assets(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
        *,
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[SourceLink]: ...

    def list_links_by_imports(
        self,
        scope: ProjectScope,
        import_ids: Sequence[UUID],
        *,
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[SourceLink]: ...

    def list_links_by_data_sources(
        self,
        scope: ProjectScope,
        data_source_ids: Sequence[UUID],
        *,
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[SourceLink]: ...


class SourceProvenanceResolver:
    """把权威表里的来源事实组装成血缘边与来源结论。

    同一批节点（同一层 BFS 前沿）合并成一次查询，因此调用方按层调用即可
    避免逐节点查询。
    """

    def __init__(
        self,
        links: SourceProvenanceRepository,
        assets: RawAssetRepository,
    ) -> None:
        self._links = links
        self._assets = assets

    # ---------- 边投影 ----------

    def source_to_import_edges(
        self,
        scope: ProjectScope,
        *,
        source_ids: Sequence[UUID] = (),
        import_ids: Sequence[UUID] = (),
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[LineageEdge]:
        """``DATA_SOURCE --PRODUCED--> DATA_SOURCE_IMPORT``。

        ``source_ids`` 用于正向（从数据源出发），``import_ids`` 用于反向
        （从导入任务回溯数据源）。两者互斥，同时为空返回空列表。
        """
        _require_single_filter(source_ids=source_ids, import_ids=import_ids)
        rows: list[SourceLink] = []
        if source_ids:
            rows = self._links.list_links_by_data_sources(scope, source_ids, limit=limit)
        elif import_ids:
            rows = self._links.list_links_by_imports(scope, import_ids, limit=limit)
        grouped: dict[tuple[UUID, UUID], SourceLink] = {}
        for link in rows:
            grouped.setdefault((link.data_source_id, link.import_id), link)
        return [
            self._edge(
                project_id=link.project_id,
                from_type=LineageNodeType.DATA_SOURCE,
                from_id=link.data_source_id,
                to_type=LineageNodeType.DATA_SOURCE_IMPORT,
                to_id=link.import_id,
                created_by=link.import_created_by,
                created_at=link.import_created_at,
                metadata={
                    "derived": True,
                    "derived_from": "data_source_imports",
                    "data_source": {
                        "data_source_id": str(link.data_source_id),
                        "code": link.data_source_code,
                        "name": link.data_source_name,
                        "kind": link.data_source_kind,
                        "repo_id": link.repo_id,
                        "revision": link.revision,
                    },
                    "import": {
                        "import_id": str(link.import_id),
                        "status": link.import_status,
                        "scope": link.import_scope,
                        "started_at": _iso(link.import_started_at),
                        "finished_at": _iso(link.import_finished_at),
                        "imported_file_count": link.imported_file_count,
                    },
                },
            )
            for link in sorted(grouped.values(), key=_link_order)
        ]

    def import_to_asset_edges(
        self,
        scope: ProjectScope,
        *,
        import_ids: Sequence[UUID] = (),
        asset_ids: Sequence[UUID] = (),
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[LineageEdge]:
        """``DATA_SOURCE_IMPORT --PRODUCED--> RAW_ASSET``。

        ``import_ids`` 用于正向（导入任务产出了哪些资产），``asset_ids`` 用于
        反向（这条资产由哪个导入任务产出）。没有关联 RawAsset 的历史媒体行
        （``raw_asset_id`` 为空）不产边——没有可指向的节点，硬造一个就是伪造。
        """
        _require_single_filter(import_ids=import_ids, asset_ids=asset_ids)
        rows: list[SourceLink] = []
        if import_ids:
            rows = self._links.list_links_by_imports(scope, import_ids, limit=limit)
        elif asset_ids:
            rows = self._links.list_links_by_raw_assets(scope, asset_ids, limit=limit)
        grouped: dict[tuple[UUID, UUID], SourceLink] = {}
        for link in rows:
            if link.raw_asset_id is None:
                continue
            key = (link.import_id, link.raw_asset_id)
            current = grouped.get(key)
            # 同一导入内同一资产有多行媒体（多机位）时取最早一行，保证结果确定。
            if current is None or _media_order(link) < _media_order(current):
                grouped[key] = link
        return [self._import_asset_edge(link) for link in sorted(grouped.values(), key=_link_order)]

    def _import_asset_edge(self, link: SourceLink) -> LineageEdge:
        asset_id = link.raw_asset_id
        if asset_id is None:
            # 调用方已过滤空值；走到这里说明分组逻辑被改坏了，不能静默产边。
            raise DomainValidationError("导入任务未关联 RawAsset，不应生成投影边")
        return self._edge(
            project_id=link.project_id,
            from_type=LineageNodeType.DATA_SOURCE_IMPORT,
            from_id=link.import_id,
            to_type=LineageNodeType.RAW_ASSET,
            to_id=asset_id,
            # 边的操作者/时间取自媒体资产行本身：那条行就是"导入产出了该资产"的事实。
            created_by=link.media_created_by or link.import_created_by,
            created_at=link.media_created_at or link.import_created_at,
            metadata={
                "derived": True,
                "derived_from": "data_source_media_assets",
                "media_asset_id": str(link.media_asset_id),
                "data_source_id": str(link.data_source_id),
                "import_id": str(link.import_id),
                "episode_key": link.episode_key,
                "source_path": link.source_path,
                "camera": link.camera,
            },
        )

    # ---------- 来源结论 ----------

    def origins(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
        *,
        known: Mapping[UUID, Sequence[SourceLink]] | None = None,
        limit: int = DEFAULT_PROJECTION_LIMIT,
    ) -> list[RawAssetOrigin]:
        """给出每条资产的来源状态；``known`` 可复用已查到的关联行，避免重复查询。"""
        unique_ids = tuple(dict.fromkeys(asset_ids))
        if not unique_ids:
            return []
        collected: dict[UUID, list[SourceLink]] = {
            asset_id: list(rows) for asset_id, rows in (known or {}).items()
        }
        missing = [asset_id for asset_id in unique_ids if asset_id not in collected]
        if missing:
            for link in self._links.list_links_by_raw_assets(scope, missing, limit=limit):
                if link.raw_asset_id is None:
                    continue
                collected.setdefault(link.raw_asset_id, []).append(link)
        source_types = self._assets.list_source_types(scope, unique_ids)
        return [
            RawAssetOrigin(
                raw_asset_id=asset_id,
                status=classify_raw_asset_source(
                    source_type=source_types.get(asset_id),
                    linked=bool(collected.get(asset_id)),
                ),
                links=tuple(sorted(collected.get(asset_id, ()), key=_link_order)),
            )
            for asset_id in unique_ids
        ]

    # ---------- 内部 ----------

    def _edge(
        self,
        *,
        project_id: UUID,
        from_type: LineageNodeType,
        from_id: UUID,
        to_type: LineageNodeType,
        to_id: UUID,
        created_by: UUID,
        created_at: datetime,
        metadata: Mapping[str, object],
    ) -> LineageEdge:
        # 端点固定，派生 id 才稳定；同一端点+关系在同一项目内唯一。
        key = (
            f"{project_id}:{from_type.value}:{from_id}:"
            f"{to_type.value}:{to_id}:{LineageRelation.PRODUCED.value}"
        )
        return LineageEdge(
            edge_id=uuid5(_DERIVED_EDGE_NAMESPACE, key),
            project_id=project_id,
            from_node_type=from_type,
            from_node_id=from_id,
            to_node_type=to_type,
            to_node_id=to_id,
            relation=LineageRelation.PRODUCED,
            created_by=created_by,
            created_at=created_at,
            metadata=dict(metadata),
        )


def _require_single_filter(**filters: Sequence[UUID]) -> None:
    """两种过滤维度互斥：同时传入说明调用方搞错了方向，不能静默取其一。"""
    if sum(1 for ids in filters.values() if ids) > 1:
        names = " / ".join(name for name in filters)
        raise DomainValidationError(f"{names} 只能传一个")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _link_order(link: SourceLink) -> tuple[datetime, str, str]:
    return (link.import_created_at, str(link.data_source_id), str(link.import_id))


def _media_order(link: SourceLink) -> tuple[datetime, str]:
    return (link.media_created_at or link.import_created_at, str(link.media_asset_id))
