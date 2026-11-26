"""语义近似去重用例（V52-031，蓝图 G6 / N1 训练集防污染）。

## 职责边界

- **只读**：本用例不写任何行、不删任何数据；G6 这一版只回答
  "这段文本与项目内既有样本是否近似重复"。
- **后端可插拔的装配点**：:meth:`SemanticDedupUseCases.capabilities` 把
  仓储探测到的真实能力（pg_trgm / pgvector）翻译成对外的可用性视图；
  :meth:`SemanticDedupUseCases.search` 在真正检索前确认默认后端
  （``TRIGRAM``）可用，不可用时抛 :class:`DedupBackendUnavailableError`，
  **不静默返回空结果**——"没查"与"查过没重复"是两种完全不同的结论。
- **领域权威**：仓储只负责召回（bounded、可能多召回），最终分数与阈值判定
  由 :func:`find_near_duplicates` 用领域纯函数重算。

## VECTOR 后端的诚实口径

``VECTOR`` 在本版本**没有实现**。可用性视图里它恒为 ``available=false``，
原因区分两种：``extension_missing``（数据库没装 pgvector）与
``not_implemented``（扩展在、代码未实现）。客户端不得据此认为"向量能力可用"。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.annotation.semantic_dedup import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_THRESHOLD,
    DedupBackend,
    DedupBackendUnavailableError,
    NearDuplicateCandidate,
    assert_backend_available,
    find_near_duplicates,
    normalize_text,
    text_digest,
    validate_candidate_limit,
    validate_threshold,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.semantic_dedup import SqlSemanticDedupRepository

_TRIGRAM_MISSING_REASON = "TRIGRAM 后端不可用：PostgreSQL 未安装 pg_trgm 扩展（迁移 0084 应已安装）"

#: 当前生效后端：部署事实是"pg_trgm 可用、pgvector 不可用"，因此恒为 TRIGRAM。
#: 将来要切 VECTOR 必须先实现向量检索并显式改这里，禁止静默切换。
_ACTIVE_BACKEND = DedupBackend.TRIGRAM


@dataclass(frozen=True, slots=True)
class DedupBackendStatus:
    """单个后端的可用性（``reason`` 只在不可用时给出）。"""

    backend: DedupBackend
    available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DedupCapabilities:
    """去重能力视图（GET 响应直接消费）。"""

    engine: str
    active_backend: DedupBackend
    threshold: float
    candidate_limit: int
    backends: tuple[DedupBackendStatus, ...]


@dataclass(frozen=True, slots=True)
class DedupQueryResult:
    """一次近似重复查询的结论（POST 响应直接消费）。"""

    text_digest: str
    backend: DedupBackend
    threshold: float
    scanned: int
    candidates: tuple[NearDuplicateCandidate, ...]


class SemanticDedupUseCases:
    """近似去重能力状态与只读查询。"""

    def __init__(
        self,
        repository: SqlSemanticDedupRepository,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> None:
        self._repository = repository
        self._threshold = validate_threshold(threshold)
        self._candidate_limit = validate_candidate_limit(candidate_limit)

    def capabilities(self, scope: ProjectScope) -> DedupCapabilities:
        """返回当前后端的真实可用性、生效阈值与候选窗口（不触发检索）。"""
        flags = self._repository.backend_flags()
        scope.ensure(scope.project_id)
        trigram = DedupBackendStatus(
            backend=DedupBackend.TRIGRAM,
            available=flags.has_pg_trgm,
            reason=None if flags.has_pg_trgm else _TRIGRAM_MISSING_REASON,
        )
        return DedupCapabilities(
            engine=flags.engine,
            active_backend=_ACTIVE_BACKEND,
            threshold=self._threshold,
            candidate_limit=self._candidate_limit,
            backends=(trigram, _vector_status(flags.has_vector_extension)),
        )

    def search(
        self,
        scope: ProjectScope,
        text: str,
        *,
        threshold: float | None = None,
        limit: int | None = None,
    ) -> DedupQueryResult:
        """查询一段文本在项目内的近似重复样本（只读，不落库）。"""
        normalized = normalize_text(text)
        resolved_threshold = validate_threshold(self._threshold if threshold is None else threshold)
        resolved_limit = validate_candidate_limit(self._candidate_limit if limit is None else limit)
        flags = self._repository.backend_flags()
        if not flags.has_pg_trgm:
            raise DedupBackendUnavailableError(
                _TRIGRAM_MISSING_REASON,
                backend=DedupBackend.TRIGRAM,
                reason="extension_missing",
            )
        candidates, scanned = self._repository.search(
            scope,
            normalized,
            threshold=resolved_threshold,
            limit=resolved_limit,
        )
        matched = find_near_duplicates(normalized, candidates, resolved_threshold)
        return DedupQueryResult(
            text_digest=text_digest(normalized),
            backend=_ACTIVE_BACKEND,
            threshold=resolved_threshold,
            scanned=scanned,
            candidates=matched,
        )


def _vector_status(has_vector_extension: bool) -> DedupBackendStatus:
    """把 VECTOR 的不可用原因翻译成对外视图（接口预留，恒不可用）。

    ``assert_backend_available`` 是"请求使用该后端时的唯一校验"，这里复用它
    构造状态，保证"GET 说不可用"与"POST 真报错"是同一份原因文案，不会两处漂移。
    """
    try:
        assert_backend_available(DedupBackend.VECTOR, has_vector_extension)
    except DedupBackendUnavailableError as exc:
        return DedupBackendStatus(
            backend=DedupBackend.VECTOR,
            available=False,
            reason=str(exc),
        )
    # 向量检索一旦真正实现，上面的 assert 不再抛错，这里才会走到。
    return DedupBackendStatus(backend=DedupBackend.VECTOR, available=True)


__all__ = [
    "DedupBackendStatus",
    "DedupCapabilities",
    "DedupQueryResult",
    "SemanticDedupUseCases",
]
