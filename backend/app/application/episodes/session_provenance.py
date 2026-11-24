"""上传会话链的只读投影：把 ``raw_asset_links`` 展开成血缘边。

设计取舍（与数据源链同一套判断，见 ``application/episodes/provenance.py``）：

1. **权威事实只存一处**。「这条资产由哪个上传会话登记」的事实已经在
   ``raw_asset_links``（``link_type=UPLOAD_SESSION``，含 ``upload_session_id``
   外键）与 ``raw_assets.upload_session_id`` 里，再往 ``data_lineage_edges``
   写一份必然漂移；
2. **历史数据同等可见**。写时投影只覆盖上线后新完成的上传；
3. **不伪造真实数据**。投影边的 ``created_by`` / ``created_at`` 取自链接行
   本身（那条行就是"会话登记了该资产"的事实），``metadata.derived=true``
   与持久化边区分；边 id 由端点确定性派生（``uuid5``）。

为什么边方向是 ``RAW_ASSET --UPLOADED_BY--> UPLOAD_SESSION``：沿用
``domain/episodes/lineage.py`` 的 ``RELATION_ENDPOINTS`` 与
``docs/architecture/阶段2-Episode血缘与API摘要-v1.md`` §8.4 的既定方向
（"资产由会话登记"）。它表示"会话是登记者"，在遍历语义里位于资产下游，
因此从资产正向（``forward`` / ``OUT``）1 跳可达；从 DatasetVersion 追溯
会话需要"先反向取资产，再从资产正向取会话"两段组合。

本模块只做「读 + 组装」，不写库。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.lineage import LineageEdge
from app.domain.identity.models import ProjectScope

# 确定性边 id 的命名空间：固定常量，改动它等于让所有投影边换 id。
# 与数据源链的 ``_DERIVED_EDGE_NAMESPACE`` 不同——两套投影各用各的命名空间，
# 避免"同一对端点在不同投影里撞 id"。
_DERIVED_EDGE_NAMESPACE = UUID("c2f5a1d4-6b0e-4c7a-8f31-5e9d20a76b43")

DEFAULT_SESSION_PROJECTION_LIMIT = 100


@dataclass(frozen=True, slots=True)
class SessionLink:
    """一条 ``raw_asset_links``（``UPLOAD_SESSION``）行连同会话属性。"""

    project_id: UUID
    asset_id: UUID
    upload_session_id: UUID
    link_id: UUID
    link_status: str
    session_status: str
    session_original_filename: str
    created_by: UUID
    created_at: datetime
    # 会话可继续追到采集会话（``capture_sessions``）。血缘节点类型里没有
    # ``CAPTURE_SESSION``（新增要迁移 + 契约变更），因此把权威 ID 放进边的
    # metadata：客户端可用 ``GET /capture-sessions/{id}`` 继续，不需要伪造节点。
    capture_session_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "asset_id", "upload_session_id", "link_id", "created_by"):
            if not isinstance(getattr(self, name), UUID):
                raise DomainValidationError(f"{name} 必须是 UUID")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise DomainValidationError("created_at 必须是带时区的 datetime")


class SessionProvenanceRepository(Protocol):
    """上传会话链只读端口；实现必须硬绑 ``scope.project_id``。"""

    def list_links_by_assets(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
        *,
        limit: int = DEFAULT_SESSION_PROJECTION_LIMIT,
    ) -> list[SessionLink]: ...

    def list_links_by_sessions(
        self,
        scope: ProjectScope,
        session_ids: Sequence[UUID],
        *,
        limit: int = DEFAULT_SESSION_PROJECTION_LIMIT,
    ) -> list[SessionLink]: ...

    def session_exists(self, scope: ProjectScope, session_id: UUID) -> bool: ...


class SessionProvenanceResolver:
    """把会话链事实组装成 ``UPLOADED_BY`` 投影边。"""

    def __init__(self, links: SessionProvenanceRepository) -> None:
        self._links = links

    def asset_to_session_edges(
        self,
        scope: ProjectScope,
        *,
        asset_ids: Sequence[UUID] = (),
        session_ids: Sequence[UUID] = (),
        limit: int = DEFAULT_SESSION_PROJECTION_LIMIT,
    ) -> list[LineageEdge]:
        """``RAW_ASSET --UPLOADED_BY--> UPLOAD_SESSION``。

        ``asset_ids`` 用于正向（资产由哪些会话登记），``session_ids`` 用于
        反向（某个会话登记了哪些资产）。两者互斥，同时传入是调用方搞错了
        方向，必须显式拒绝而不是静默取其一。
        """
        if asset_ids and session_ids:
            raise DomainValidationError("asset_ids / session_ids 只能传一个")
        rows: list[SessionLink] = []
        if asset_ids:
            rows = self._links.list_links_by_assets(scope, asset_ids, limit=limit)
        elif session_ids:
            rows = self._links.list_links_by_sessions(scope, session_ids, limit=limit)
        grouped: dict[tuple[UUID, UUID], SessionLink] = {}
        for link in rows:
            # 同一 (资产, 会话) 理论上只有一行（唯一约束）；重复时取最早一行，
            # 让投影结果与查询顺序无关。
            key = (link.asset_id, link.upload_session_id)
            current = grouped.get(key)
            if current is None or (link.created_at, str(link.link_id)) < (
                current.created_at,
                str(current.link_id),
            ):
                grouped[key] = link
        return [self._edge(link) for link in sorted(grouped.values(), key=_link_order)]

    def _edge(self, link: SessionLink) -> LineageEdge:
        key = (
            f"{link.project_id}:{LineageNodeType.RAW_ASSET.value}:{link.asset_id}:"
            f"{LineageNodeType.UPLOAD_SESSION.value}:{link.upload_session_id}:"
            f"{LineageRelation.UPLOADED_BY.value}"
        )
        return LineageEdge(
            edge_id=uuid5(_DERIVED_EDGE_NAMESPACE, key),
            project_id=link.project_id,
            from_node_type=LineageNodeType.RAW_ASSET,
            from_node_id=link.asset_id,
            to_node_type=LineageNodeType.UPLOAD_SESSION,
            to_node_id=link.upload_session_id,
            relation=LineageRelation.UPLOADED_BY,
            created_by=link.created_by,
            created_at=link.created_at,
            metadata={
                "derived": True,
                "derived_from": "raw_asset_links",
                "link_id": str(link.link_id),
                "link_status": link.link_status,
                "upload_session_id": str(link.upload_session_id),
                "upload_session_status": link.session_status,
                "upload_original_filename": link.session_original_filename,
                "capture_session_id": (
                    str(link.capture_session_id) if link.capture_session_id is not None else None
                ),
            },
        )


def _link_order(link: SessionLink) -> tuple[datetime, str, str]:
    return (link.created_at, str(link.asset_id), str(link.upload_session_id))


__all__ = [
    "DEFAULT_SESSION_PROJECTION_LIMIT",
    "SessionLink",
    "SessionProvenanceRepository",
    "SessionProvenanceResolver",
]
