"""标注提交质量检查 HTTP 路由：触发与查询。

只读动作走 ``ReadScope``，触发动作走 ``WriteScope``（viewer 被拒）。项目隔离
由 ``ProjectScope`` 强制：路径里的 ``project_id`` 只用于鉴权定位，资源归属
在用例内按 ``scope.project_id`` 过滤，跨项目一律 404 而不是 403 之外的可探测差异。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.annotation_common import ReadScope, WriteScope, request_id
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.annotation_quality_check_models import QualityCheckOut
from app.api.v1.schemas import PageOut

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-quality"])


@router.post(
    "/annotation-submissions/{submission_id}/quality-checks",
    response_model=QualityCheckOut,
    status_code=201,
)
def run_quality_check(
    submission_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> QualityCheckOut:
    """对提交执行一次质量检查，落一条执行记录与逐条记录结果。

    刻意不支持 ``Idempotency-Key`` 幂等：一次检查是**只追加且结论确定**的操作，
    重复触发得到的是两条语义相同的记录而不是副作用。若强行幂等，反而会让
    "提交内容变更后重跑得到不同结论"看起来像错误。
    """
    run = use_cases.quality_checks.run(
        scope,
        submission_id,
        request_id=request_id(request),
    )
    return QualityCheckOut.from_domain(run)


@router.get(
    "/annotation-submissions/{submission_id}/quality-checks",
    response_model=PageOut[QualityCheckOut],
)
def list_quality_checks(
    submission_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[QualityCheckOut]:
    items = use_cases.quality_checks.list_for_submission(
        scope,
        submission_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.run_id)
    return PageOut(
        items=[QualityCheckOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
