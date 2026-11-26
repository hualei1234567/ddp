"""语义近似去重仓储（V52-031，蓝图 G6 / N1 训练集防污染）。

## 语料与载体

去重语料是项目内的**样本文本**：``episodes.language_instruction``
（``annotation_sample_episodes`` 引用的样本载体，见迁移 0084 的选型说明）。
仓储只读，不写任何行——G6 这一版是**只读能力**，去重结论不落库、不删数据。

## 两条实现路径（同一阈值语义）

- **PostgreSQL**：``pg_trgm`` 扩展 + ``language_instruction`` 的 GIN 三元组
  索引。用 ``%`` 运算符做**索引扫描**预筛（阈值通过 ``set_config`` 设成
  本次请求阈值），再按 ``similarity()`` 倒序取有界窗口；``text_digest`` 的
  B-tree 索引负责**完全重复**的精确命中（``BitmapOr`` 可同时用上两个索引）。
- **SQLite**（集成测试/本地）：没有 pg_trgm，退化为
  :meth:`SqlSemanticDedupRepository.prepare_candidates` 有界拉取 + 领域层
  纯函数算分。**有界**是硬约束：禁止全表拉取，窗口大小由 ``limit`` 封顶。

两条路径的分数最终都由 :func:`find_near_duplicates` 用领域纯函数重算，
因此"领域层是唯一权威判定"；PostgreSQL 侧只负责**召回**（可能多召回、
不会因为 DB 估算偏差把领域认为重复的候选静默丢掉——阈值只收窄召回上界，
详见领域模块能力边界）。

## 后端探测（不假装能力）

:meth:`backend_flags` 是唯一探测点：PG 查 ``pg_extension``（``pg_trgm`` /
``vector``）；SQLite 恒为"TRIGRAM 可用、VECTOR 不可用"。**不猜**、不缓存
到进程级——扩展可能被运维刚装上，每次请求探测的代价是两条系统目录查询。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Row, func, or_, select, true
from sqlalchemy.orm import Session

from app.domain.annotation.semantic_dedup import (
    DEFAULT_CANDIDATE_LIMIT,
    NearDuplicateCandidate,
    text_digest,
    validate_candidate_limit,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel

#: 空指令 / 纯空白指令不进去重语料：它们没有可比较的 3-gram。
_NON_EMPTY_INSTRUCTION = func.length(func.trim(EpisodeModel.language_instruction)) > 0


@dataclass(frozen=True, slots=True)
class DedupBackendFlags:
    """当前连接的后端能力探测结果（供应用层装配可用性视图）。"""

    engine: str
    has_pg_trgm: bool
    has_vector_extension: bool


class SqlSemanticDedupRepository:
    """``episodes.language_instruction`` 语料的只读近似去重仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # 后端探测
    # ------------------------------------------------------------------ #
    def backend_flags(self) -> DedupBackendFlags:
        """探测当前连接真正的去重能力（不猜测、不缓存）。"""
        dialect = self._session.get_bind().dialect.name
        if dialect != "postgresql":
            # SQLite 走领域纯函数兜底，不依赖任何扩展；向量能力恒不可用。
            return DedupBackendFlags(
                engine=dialect,
                has_pg_trgm=True,
                has_vector_extension=False,
            )
        return DedupBackendFlags(
            engine=dialect,
            has_pg_trgm=self._extension_installed("pg_trgm"),
            has_vector_extension=self._extension_installed("vector"),
        )

    def _extension_installed(self, extension_name: str) -> bool:
        """查 ``pg_extension``（只读系统目录；扩展名是常量，仍用绑定参数）。"""
        row = self._session.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = :name"),
            {"name": extension_name},
        ).first()
        return row is not None

    # ------------------------------------------------------------------ #
    # 有界候选拉取（SQLite 回退路径；也是未来 VECTOR 后端的取数入口）
    # ------------------------------------------------------------------ #
    def prepare_candidates(
        self,
        scope: ProjectScope,
        limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> tuple[NearDuplicateCandidate, ...]:
        """有界拉取项目内既有样本文本（按最近更新时间倒序，禁止全表）。

        ``limit`` 必须为正整数且不超过 :data:`MAX_CANDIDATE_LIMIT`；
        排序固定为 ``(updated_at DESC, id)``，同一数据快照多次调用结果一致。
        """
        scope.ensure(scope.project_id)
        bounded_limit = validate_candidate_limit(limit)
        rows = self._session.execute(
            select(
                EpisodeModel.id,
                EpisodeModel.language_instruction,
                EpisodeModel.text_digest,
            )
            .where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.language_instruction.is_not(None),
                _NON_EMPTY_INSTRUCTION,
            )
            .order_by(EpisodeModel.updated_at.desc(), EpisodeModel.id)
            .limit(bounded_limit)
        ).all()
        return tuple(_to_candidate(row) for row in rows)

    # ------------------------------------------------------------------ #
    # 检索入口（按方言分派）
    # ------------------------------------------------------------------ #
    def search(
        self,
        scope: ProjectScope,
        text: str,
        *,
        threshold: float,
        limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> tuple[tuple[NearDuplicateCandidate, ...], int]:
        """返回 ``(候选, 实际扫描条数)``；分数由领域层重算，这里只做召回。

        ``text`` / ``threshold`` 必须已被应用层校验（本方法只负责 DB 语义）。
        """
        scope.ensure(scope.project_id)
        bounded_limit = validate_candidate_limit(limit)
        if self._session.get_bind().dialect.name == "postgresql":
            return self._search_postgres(scope, text, threshold=threshold, limit=bounded_limit)
        candidates = self.prepare_candidates(scope, bounded_limit)
        return candidates, len(candidates)

    def _search_postgres(
        self,
        scope: ProjectScope,
        text: str,
        *,
        threshold: float,
        limit: int,
    ) -> tuple[tuple[NearDuplicateCandidate, ...], int]:
        """pg_trgm 索引扫描召回：``%`` 走 GIN，``text_digest`` 走 B-tree。"""
        self._session.execute(
            select(
                func.set_config(
                    "pg_trgm.similarity_threshold",
                    sa.literal(f"{threshold:.6f}"),
                    true(),
                )
            )
        )
        similarity = func.similarity(EpisodeModel.language_instruction, text).label("score")
        rows = self._session.execute(
            select(
                EpisodeModel.id,
                EpisodeModel.language_instruction,
                EpisodeModel.text_digest,
                similarity,
            )
            .where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.language_instruction.is_not(None),
                _NON_EMPTY_INSTRUCTION,
                or_(
                    # 完全重复：B-tree 精确命中（历史行由迁移 0084 回填）。
                    EpisodeModel.text_digest == text_digest(text),
                    # 近似重复：pg_trgm GIN 索引扫描，阈值 = 本次请求阈值。
                    EpisodeModel.language_instruction.op("%")(text),
                ),
            )
            .order_by(similarity.desc(), EpisodeModel.id)
            .limit(limit)
        ).all()
        return tuple(_to_candidate(row) for row in rows), len(rows)


def _to_candidate(row: Row[tuple[object, ...]]) -> NearDuplicateCandidate:
    """查询行 → 领域候选；``text_digest`` 为空时按需现算（不写库）。"""
    instruction = str(row.language_instruction)
    stored_digest = row.text_digest
    return NearDuplicateCandidate(
        text_digest=str(stored_digest) if stored_digest else text_digest(instruction),
        text=instruction,
        score=0.0,
        episode_id=row.id,
    )


__all__ = ["DedupBackendFlags", "SqlSemanticDedupRepository"]
