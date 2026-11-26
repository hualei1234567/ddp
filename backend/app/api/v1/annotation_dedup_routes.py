"""语义近似去重路由（V52-031，蓝图 G6 / N1 训练集防污染）。

两个端点，**都是只读能力**（不写、不删数据）：

- ``GET  /projects/{id}/annotation-dedup``：当前后端可用性、生效阈值与候选窗口；
- ``POST /projects/{id}/annotation-dedup``：给一段文本查项目内近似重复样本。

## 为什么用 POST 查

待查文本最长 2000 字符，放 query string 会撞 URL 长度限制并把文本写进访问
日志；POST 只承载查询语义，**不产生任何副作用**。权限按 READ 校验
（``get_project_scope``）：viewer 也可以查，"谁能改数据"与"谁能读能力状态"
是两件事。

## 契约与错误

路径只进 fragments（``paths.ops.yaml`` 增补 ``annotationDedup``），**不 bump
openapi.yaml version**（版本由主控统一治理）。

VECTOR 后端不可用**不是** HTTP 错误：``GET`` 用 ``available=false`` + reason
如实告知；``POST`` 永远使用默认 ``TRIGRAM`` 后端，若数据库连 pg_trgm 都缺失
（部署事故），领域层抛 ``validation_failed``（422）并带 ``details``——不静默
返回空列表冒充"没有重复"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_project_scope
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.domain.annotation.semantic_dedup import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_THRESHOLD,
    MAX_CANDIDATE_LIMIT,
    MAX_TEXT_LENGTH,
)
from app.domain.identity.models import ProjectScope

router = APIRouter(tags=["annotation-dedup"], prefix="/projects/{project_id}/annotation-dedup")

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]


class AnnotationDedupBackendOut(BaseModel):
    """单个后端的可用性。"""

    backend: str = Field(description="后端标识：TRIGRAM / VECTOR")
    available: bool = Field(description="当前部署下该后端是否真实可用")
    reason: str | None = Field(
        default=None,
        description="不可用原因（available=true 时为 null）；VECTOR 恒不可用",
    )


class AnnotationDedupStatusOut(BaseModel):
    """去重能力状态（后端、阈值、候选窗口）。"""

    engine: str = Field(description="当前数据库方言：postgresql / sqlite")
    backend: str = Field(description="当前生效后端（本版本恒为 TRIGRAM）")
    threshold: float = Field(description="默认命中阈值（>= 命中）")
    candidate_limit: int = Field(description="默认候选窗口（有界召回，禁止全表）")
    backends: list[AnnotationDedupBackendOut] = Field(description="全部后端的可用性")


class AnnotationDedupQueryIn(BaseModel):
    """查询请求：一段待查文本 + 可选阈值/窗口覆盖。"""

    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="待查文本（1..2000 字符；与样本文本上限同口径）",
    )
    threshold: float = Field(
        default=DEFAULT_THRESHOLD,
        ge=0,
        le=1,
        description="命中阈值，score >= threshold 算重复（默认 0.85）",
    )
    limit: int = Field(
        default=DEFAULT_CANDIDATE_LIMIT,
        ge=1,
        le=MAX_CANDIDATE_LIMIT,
        description="参与比较的候选窗口上限（默认 200，最大 500）",
    )


class AnnotationDedupCandidateOut(BaseModel):
    """一条近似重复候选。"""

    text_digest: str = Field(description="候选文本的 sha256 指纹")
    text: str = Field(description="候选文本（规范化后）")
    score: float = Field(description="与查询文本的 trigram 相似度（0..1，6 位小数）")
    episode_id: str | None = Field(default=None, description="候选样本载体（Episode）ID")


class AnnotationDedupResultOut(BaseModel):
    """一次查询的结论（只读，不落库）。"""

    text_digest: str = Field(description="查询文本的 sha256 指纹")
    backend: str = Field(description="实际使用的后端（本版本恒为 TRIGRAM）")
    threshold: float = Field(description="本次生效阈值")
    scanned: int = Field(description="实际参与比较的候选条数（有界窗口内）")
    candidate_count: int = Field(description="命中条数")
    candidates: list[AnnotationDedupCandidateOut] = Field(
        description="命中的近似重复候选（按分数降序）"
    )


@router.get(
    "",
    response_model=AnnotationDedupStatusOut,
    summary="语义近似去重能力状态（后端可用性、阈值、候选窗口）",
)
def get_annotation_dedup_status(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationDedupStatusOut:
    capabilities = use_cases.semantic_dedup.capabilities(scope)
    return AnnotationDedupStatusOut(
        engine=capabilities.engine,
        backend=capabilities.active_backend.value,
        threshold=capabilities.threshold,
        candidate_limit=capabilities.candidate_limit,
        backends=[
            AnnotationDedupBackendOut(
                backend=status.backend.value,
                available=status.available,
                reason=status.reason,
            )
            for status in capabilities.backends
        ],
    )


@router.post(
    "",
    response_model=AnnotationDedupResultOut,
    summary="查询一段文本在项目内的近似重复样本（只读，不落库）",
)
def query_annotation_dedup(
    scope: ReadScope,
    payload: AnnotationDedupQueryIn,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationDedupResultOut:
    result = use_cases.semantic_dedup.search(
        scope,
        payload.text,
        threshold=payload.threshold,
        limit=payload.limit,
    )
    return AnnotationDedupResultOut(
        text_digest=result.text_digest,
        backend=result.backend.value,
        threshold=result.threshold,
        scanned=result.scanned,
        candidate_count=len(result.candidates),
        candidates=[
            AnnotationDedupCandidateOut(
                text_digest=candidate.text_digest,
                text=candidate.text,
                score=candidate.score,
                episode_id=None if candidate.episode_id is None else str(candidate.episode_id),
            )
            for candidate in result.candidates
        ],
    )
