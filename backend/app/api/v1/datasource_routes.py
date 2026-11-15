"""数据源 HTTP 路由：登记外部数据集、查询与导入任务编排。

只做协议适配；许可证与 gated 一致性、状态机都在领域/用例层。
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DbSession
from app.api.v1.dataset_dependencies import (
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.datasource_schemas import (
    DataSourceCreate,
    DataSourceImportCreate,
    DataSourceImportOut,
    DataSourceOut,
    DataSourcePatch,
    ImportActionRequest,
    ImportEpisodeMaterializeRequest,
    ImportEpisodeMaterializeResultOut,
    MaterializedEpisodeOut,
    MediaAssetListOut,
    MediaAssetOut,
    MediaAssetPlaybackOut,
    QualityReportOut,
    ReviewCreateOut,
    ReviewDecisionRequest,
)
from app.api.v1.episode_dependencies import build_episode_service
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import PageOut
from app.application.datasources import (
    DataSourcePatch as DataSourcePatchCmd,
)
from app.application.datasources import (
    DataSourceUseCases,
    NewDataSource,
    NewDataSourceImport,
)
from app.application.datasources.episode_materialization import (
    ImportEpisodeMaterializer,
)
from app.application.datasources.ports import (
    AuditRepository,
    DataReviewRepository,
    DataSourceImportRepository,
    DataSourceRepository,
    IdempotencyRepository,
    MediaAssetRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasources.reviews import DataReviewUseCases
from app.core.config import get_settings
from app.domain.datasources.enums import ImportStatus
from app.domain.datasources.errors import DomainValidationError, ResourceNotFoundError
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataReviewRepository,
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import SqlEpisodeRepository
from app.infrastructure.repositories.idempotency import SqlIdempotencyRepository
from app.infrastructure.storage.data_source_objects import DataSourceObjectReader
from app.infrastructure.storage.s3 import S3MultipartStorage

router = APIRouter(prefix="/projects/{project_id}", tags=["datasources"])

ExpectedVersion = Annotated[int, Depends(require_expected_version)]


def get_data_source_use_cases(session: DbSession) -> DataSourceUseCases:
    return DataSourceUseCases(
        sources=_cast_source(SqlDataSourceRepository(session)),
        imports=_cast_import(SqlDataSourceImportRepository(session)),
        audit=_cast_audit(SqlDatasetAuditRepository(session)),
        outbox=_cast_outbox(SqlDatasetOutboxRepository(session)),
        idempotency=_cast_idem(SqlIdempotencyRepository(session)),
        uow=_cast_uow(session),
    )


# 适配器只做类型标注的桥接：运行时同一对象，静态检查对齐端口协议。
def _cast_source(value: object) -> DataSourceRepository:
    return value  # type: ignore[return-value]


def _cast_import(value: object) -> DataSourceImportRepository:
    return value  # type: ignore[return-value]


def _cast_audit(value: object) -> AuditRepository:
    return value  # type: ignore[return-value]


def _cast_outbox(value: object) -> OutboxRepository:
    return value  # type: ignore[return-value]


def _cast_idem(value: object) -> IdempotencyRepository:
    return value  # type: ignore[return-value]


def _cast_uow(value: object) -> UnitOfWork:
    return value  # type: ignore[return-value]


DataSourceServicesDep = Annotated[DataSourceUseCases, Depends(get_data_source_use_cases)]


def get_review_use_cases(session: DbSession) -> DataReviewUseCases:
    return DataReviewUseCases(
        reviews=_as_review_repo(SqlDataReviewRepository(session)),
        imports=_cast_import(SqlDataSourceImportRepository(session)),
        assets=_as_asset_repo(SqlMediaAssetRepository(session)),
        audit=_cast_audit(SqlDatasetAuditRepository(session)),
        outbox=_cast_outbox(SqlDatasetOutboxRepository(session)),
        uow=_cast_uow(session),
    )


def _as_review_repo(value: object) -> DataReviewRepository:
    return value  # type: ignore[return-value]


def _as_asset_repo(value: object) -> MediaAssetRepository:
    return value  # type: ignore[return-value]


ReviewServicesDep = Annotated[DataReviewUseCases, Depends(get_review_use_cases)]


def get_episode_materializer(session: DbSession) -> ImportEpisodeMaterializer:
    """导入产物 -> Episode 的物化用例。

    Episode 侧的 8 个协作者由 ``build_episode_service`` 统一装配，不在这里
    重抄一遍——重抄就多了一处会漂移的接线。
    """
    return ImportEpisodeMaterializer(
        imports=_cast_import(SqlDataSourceImportRepository(session)),
        assets=_as_asset_repo(SqlMediaAssetRepository(session)),
        episodes=build_episode_service(session),
        episode_repository=SqlEpisodeRepository(session),
    )


MaterializerDep = Annotated[
    ImportEpisodeMaterializer, Depends(get_episode_materializer)
]


@router.get("/data-sources", response_model=PageOut[DataSourceOut])
def list_data_sources(
    scope: ReadScope,
    use_cases: DataSourceServicesDep,
    kind: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.list_sources(
        scope, kind=kind, after_id=decode_cursor(cursor), limit=fetch_limit(limit)
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.data_source_id)
    return {
        "items": [DataSourceOut.from_domain(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/data-sources", response_model=DataSourceOut, status_code=201)
def create_data_source(
    payload: DataSourceCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DataSourceServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    created = use_cases.create_source(
        scope,
        NewDataSource(
            code=payload.code,
            name=payload.name,
            repo_id=payload.repo_id,
            revision=payload.revision,
            license=payload.license,
            access_condition=payload.access_condition,
            is_gated=payload.is_gated,
            kind=payload.kind,
            config_name=payload.config_name,
            split=payload.split,
            dataset_card_url=payload.dataset_card_url,
            notes=payload.notes,
            metadata=payload.metadata,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DataSourceOut.from_domain(created).model_dump(mode="json")


@router.get("/data-sources/{data_source_id}", response_model=DataSourceOut)
def get_data_source(
    data_source_id: UUID, scope: ReadScope, use_cases: DataSourceServicesDep
) -> dict[str, Any]:
    item = use_cases.get_source(scope, data_source_id)
    return DataSourceOut.from_domain(item).model_dump(mode="json")


@router.patch("/data-sources/{data_source_id}", response_model=DataSourceOut)
def update_data_source(
    data_source_id: UUID,
    payload: DataSourcePatch,
    request: Request,
    scope: WriteScope,
    use_cases: DataSourceServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    updated = use_cases.update_source(
        scope,
        data_source_id,
        DataSourcePatchCmd(**payload.model_dump(exclude_unset=True)),
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return DataSourceOut.from_domain(updated).model_dump(mode="json")


@router.get("/data-source-imports", response_model=PageOut[DataSourceImportOut])
def list_imports(
    scope: ReadScope,
    use_cases: DataSourceServicesDep,
    data_source_id: UUID | None = None,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.list_imports(
        scope,
        data_source_id=data_source_id,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.import_id)
    return {
        "items": [DataSourceImportOut.from_domain(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post(
    "/data-sources/{data_source_id}/imports",
    response_model=DataSourceImportOut,
    status_code=201,
)
def create_import(
    data_source_id: UUID,
    payload: DataSourceImportCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DataSourceServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    created = use_cases.create_import(
        scope,
        data_source_id,
        NewDataSourceImport(
            scope=payload.scope,
            tool_name=payload.tool_name,
            tool_version=payload.tool_version,
            sample_limit=payload.sample_limit,
            cache_location=payload.cache_location,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DataSourceImportOut.from_domain(created).model_dump(mode="json")


# 播放 URL 有效期：审看是交互行为，短有效期限制泄漏面。
_PLAYBACK_TTL_SECONDS = 900


@router.get("/data-source-imports/{import_id}/assets", response_model=MediaAssetListOut)
def list_import_assets(
    import_id: UUID,
    scope: ReadScope,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    """列出一个导入产出的媒体资产，按 episode + 机位排序，便于逐条审看。"""
    items = SqlMediaAssetRepository(session).list_by_import(scope, import_id, limit=limit)
    return {"items": [MediaAssetOut.from_domain(item).model_dump(mode="json") for item in items]}


@router.post("/media-assets/{asset_id}/playback", response_model=MediaAssetPlaybackOut)
def presign_asset_playback(
    asset_id: UUID,
    scope: ReadScope,
    session: DbSession,
) -> dict[str, Any]:
    """签发短时播放 URL。

    用 POST 而非 GET：签发 URL 是有副作用的行为（产生一个可访问凭证），
    不应被浏览器/代理缓存或预取。
    """
    repo = SqlMediaAssetRepository(session)
    asset = repo.get(scope, asset_id)
    if asset is None:
        raise ResourceNotFoundError("媒体资产", asset_id)
    reader = DataSourceObjectReader(S3MultipartStorage(get_settings()))
    url = reader.presign_get(asset.object_key, expires_seconds=_PLAYBACK_TTL_SECONDS)
    return MediaAssetPlaybackOut(
        asset_id=asset.asset_id,
        url=url,
        expires_in_seconds=_PLAYBACK_TTL_SECONDS,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
    ).model_dump(mode="json")


@router.get("/data-source-imports/{import_id}", response_model=DataSourceImportOut)
def get_import(
    import_id: UUID, scope: ReadScope, use_cases: DataSourceServicesDep
) -> dict[str, Any]:
    item = use_cases.get_import(scope, import_id)
    return DataSourceImportOut.from_domain(item).model_dump(mode="json")


@router.post(
    "/data-source-imports/{import_id}/actions/{action}",
    response_model=DataSourceImportOut,
)
def act_on_import(
    import_id: UUID,
    action: str,
    payload: ImportActionRequest | None,
    request: Request,
    scope: WriteScope,
    use_cases: DataSourceServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    body = payload or ImportActionRequest()
    updated = use_cases.apply_import_action(
        scope,
        import_id,
        action,
        expected_version=expected_version,
        row_count=body.row_count,
        file_count=body.file_count,
        error_message=body.error_message,
        request_id=request_id(request),
    )
    return DataSourceImportOut.from_domain(updated).model_dump(mode="json")


@router.post(
    "/data-source-imports/{import_id}/review",
    response_model=ReviewCreateOut,
    status_code=201,
)
def ensure_review(
    import_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: ReviewServicesDep,
) -> dict[str, Any]:
    """为导入创建审核记录；已存在时直接返回（幂等）。"""
    review = use_cases.ensure_review(scope, import_id, request_id=request_id(request))
    return ReviewCreateOut.from_domain(review).model_dump(mode="json")


@router.get("/data-source-imports/{import_id}/review", response_model=ReviewCreateOut)
def get_review(import_id: UUID, scope: ReadScope, use_cases: ReviewServicesDep) -> dict[str, Any]:
    review = use_cases.get_review(scope, import_id)
    if review is None:
        raise ResourceNotFoundError("审核记录", import_id)
    return ReviewCreateOut.from_domain(review).model_dump(mode="json")


@router.post(
    "/data-source-imports/{import_id}/review/decisions",
    response_model=ReviewCreateOut,
)
def decide_review(
    import_id: UUID,
    payload: ReviewDecisionRequest,
    request: Request,
    scope: WriteScope,
    use_cases: ReviewServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    """给出审核结论。审核人不得是提交人（职责分离，数据库亦有兜底约束）。"""
    from app.domain.datasources.review import ReviewDecision

    decided = use_cases.decide(
        scope,
        import_id,
        ReviewDecision(payload.decision),
        reason=payload.reason,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return ReviewCreateOut.from_domain(decided).model_dump(mode="json")


@router.get(
    "/data-source-imports/{import_id}/quality-report",
    response_model=QualityReportOut,
)
def quality_report(
    import_id: UUID, scope: ReadScope, use_cases: ReviewServicesDep
) -> dict[str, Any]:
    """质量报告：规则版本、样本范围、通过率与失败原因都在响应里。"""
    report = use_cases.quality_report(scope, import_id)
    return QualityReportOut(
        import_id=report.import_id,
        rule_version=report.rule_version,
        sample_scope=report.sample_scope,
        total_assets=report.total_assets,
        total_episodes=report.total_episodes,
        passed=report.passed,
        findings=[
            {"rule_id": f.rule_id, "passed": f.passed, "detail": f.detail} for f in report.findings
        ],
        failures=[
            {"rule_id": f.rule_id, "passed": f.passed, "detail": f.detail} for f in report.failures
        ],
    ).model_dump(mode="json")


__all__ = ["router"]


@router.post(
    "/data-source-imports/{import_id}/episodes",
    response_model=ImportEpisodeMaterializeResultOut,
    status_code=201,
)
def materialize_import_episodes(
    import_id: UUID,
    scope: WriteScope,
    use_cases: DataSourceServicesDep,
    materializer: MaterializerDep,
    payload: ImportEpisodeMaterializeRequest | None = None,
) -> ImportEpisodeMaterializeResultOut:
    """把一次成功导入的媒体资产生成 Canonical Episode（按 episode_key 分组）。

    前置：导入状态必须 ``SUCCEEDED``。对失败/进行中的导入生成 Episode 等于
    把半截数据当成样本，因此显式拒绝而不是"能跑就让它跑"。
    """
    job = use_cases.get_import(scope, import_id)
    if job.status is not ImportStatus.SUCCEEDED:
        raise DomainValidationError(
            f"导入状态为 {job.status.value}，只有 SUCCEEDED 的导入才能生成 Episode"
        )
    result = materializer.materialize(
        scope,
        import_id,
        scene_labels=tuple((payload or ImportEpisodeMaterializeRequest()).scene_labels),
    )
    return ImportEpisodeMaterializeResultOut(
        import_id=result.import_id,
        episode_count=result.episode_count,
        episodes=[
            MaterializedEpisodeOut(
                episode_id=item.episode_id,
                episode_key=item.episode_key,
                raw_asset_ids=list(item.raw_asset_ids),
            )
            for item in result.created
        ],
        skipped_episode_keys=list(result.skipped_episode_keys),
    )
